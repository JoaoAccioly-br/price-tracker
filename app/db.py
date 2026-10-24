"""Camada de banco de dados híbrida: suporta PostgreSQL (Neon / produção) e SQLite (desenvolvimento local).

Se DATABASE_URL estiver configurada (ex.: postgresql://... na Neon),
o sistema se conecta ao PostgreSQL. Caso contrário, usa SQLite localmente.
"""
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Caminho padrão do SQLite para desenvolvimento local
DB_PATH = Path(
    os.environ.get("TRACKER_DB_PATH")
    or Path(__file__).resolve().parent.parent / "data" / "tracker.db"
)

# Pool leve de conexões PostgreSQL e cache em memória para app_settings
_PG_POOL: list[tuple[str, Any]] = []
_PG_POOL_LOCK = threading.Lock()
_PG_POOL_MAX = 5

_SETTINGS_CACHE: dict[str, tuple[str | None, float]] = {}
_SETTINGS_LOCK = threading.Lock()
_SETTINGS_TTL = 60.0  # segundos

_INITIALIZED_TARGETS: set[str] = set()
_INIT_LOCK = threading.Lock()

# Schema para SQLite
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'Notebook',
    target_price REAL,
    last_alert_at TEXT,
    last_alert_price REAL
);

CREATE TABLE IF NOT EXISTS listings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store        TEXT NOT NULL,
    url          TEXT NOT NULL,
    css_selector TEXT,
    use_browser  INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (product_id, url)
);

CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price       REAL NOT NULL,
    available   INTEGER NOT NULL DEFAULT 1,
    coupon_code TEXT,
    final_price REAL,
    checked_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prices_listing_time ON prices (listing_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_listings_product ON listings (product_id, active);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Schema para PostgreSQL (compatível com Neon)
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'Notebook',
    target_price DOUBLE PRECISION,
    last_alert_at TEXT,
    last_alert_price DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS listings (
    id           BIGSERIAL PRIMARY KEY,
    product_id   BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store        TEXT NOT NULL,
    url          TEXT NOT NULL,
    css_selector TEXT,
    use_browser  INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (product_id, url)
);

CREATE TABLE IF NOT EXISTS prices (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price       DOUBLE PRECISION NOT NULL,
    available   INTEGER NOT NULL DEFAULT 1,
    coupon_code TEXT,
    final_price DOUBLE PRECISION,
    checked_at  TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_prices_listing_time ON prices (listing_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_listings_product ON listings (product_id, active);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);
"""


class DBConflictError(Exception):
    """Exceção levantada em caso de violação de unicidade (compatível com IntegrityError)."""
    pass


class PostgresCursorWrapper:
    """Wrapper em volta do cursor do psycopg para fornecer a mesma interface do sqlite3.Row."""

    def __init__(self, cur, lastrowid: int | None = None):
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self) -> dict | None:
        return self._cur.fetchone()

    def fetchall(self) -> list[dict]:
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)


class PostgresConnectionWrapper:
    """Wrapper da conexão psycopg fornecendo interface compatível com sqlite3 (execute, ?, lastrowid)."""

    def __init__(self, raw_conn, pool_url: str | None = None):
        self._raw = raw_conn
        self._pool_url = pool_url
        self._tx = None

    def _prepare_sql(self, sql: str) -> tuple[str, bool]:
        """Converte marcadores '?' em '%s' (fora de literais) e adiciona RETURNING id quando necessário."""
        insert_match = re.match(r"^\s*INSERT\s+INTO\s+([a-zA-Z0-9_]+)", sql, re.IGNORECASE)
        is_insert = bool(insert_match)
        target_table = insert_match.group(1).lower() if insert_match else ""
        is_app_settings = target_table == "app_settings"
        has_returning = bool(re.search(r"\bRETURNING\b", sql, re.IGNORECASE))
        needs_returning = is_insert and not has_returning and not is_app_settings

        # Transforma datetime('now') se por acaso vier em alguma query
        sql_converted = sql.replace(
            "datetime('now')",
            "TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')",
        )
        # Substitui ? por %s apenas fora de strings literais SQL ('...' ou "...")
        sql_converted = re.sub(
            r"('[^']*'|\"[^\"]*\")|\?",
            lambda m: m.group(1) if m.group(1) is not None else "%s",
            sql_converted,
        )

        if needs_returning:
            sql_converted = f"{sql_converted.rstrip().rstrip(';')} RETURNING id"

        return sql_converted, needs_returning

    def execute(self, sql: str, params: Any = None):
        import psycopg
        from psycopg.errors import UniqueViolation

        sql_conv, had_returning = self._prepare_sql(sql)
        cur = self._raw.cursor()
        try:
            cur.execute(sql_conv, params or ())
        except UniqueViolation as err:
            raise DBConflictError(str(err)) from err
        except psycopg.IntegrityError as err:
            raise DBConflictError(str(err)) from err

        last_id = None
        if had_returning and cur.description:
            row = cur.fetchone()
            if row:
                last_id = row.get("id") if isinstance(row, dict) else row[0]

        return PostgresCursorWrapper(cur, lastrowid=last_id)

    def close(self):
        if self._tx is not None:
            try:
                self._tx.__exit__(None, None, None)
            except Exception:
                pass
            self._tx = None

        # Devolve a conexão saudável ao pool em vez de encerrar o socket TLS
        if self._pool_url and not getattr(self._raw, "closed", True) and not getattr(self._raw, "broken", False):
            with _PG_POOL_LOCK:
                if len(_PG_POOL) < _PG_POOL_MAX:
                    _PG_POOL.append((self._pool_url, self._raw))
                    return
        try:
            self._raw.close()
        except Exception:
            pass

    def commit(self):
        # Se estiver dentro de um bloco de transação com self._tx, o psycopg3 já comita ao sair
        if self._tx is not None:
            return
        self._raw.commit()

    def rollback(self):
        if self._tx is not None:
            return
        self._raw.rollback()

    def __enter__(self):
        # No sqlite3, 'with conn:' abre uma transação e comita/rollbacka no final SEM fechar a conexão.
        # No psycopg3, 'with raw_conn:' fecha a conexão ao sair!
        # Para ter o mesmo comportamento do sqlite3, usamos self._raw.transaction().
        self._tx = self._raw.transaction()
        self._tx.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._tx is not None:
            ret = self._tx.__exit__(exc_type, exc_val, exc_tb)
            self._tx = None
            return ret
        return False


def _current_db_target() -> str:
    """Identificador único do banco ativo (para escopo de cache e inicialização)."""
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url.startswith(("postgres://", "postgresql://")):
        return db_url
    return f"sqlite://{DB_PATH}"


def is_postgres() -> bool:
    """Retorna True se estiver configurado para usar PostgreSQL."""
    db_url = os.environ.get("DATABASE_URL", "").strip()
    return db_url.startswith("postgres://") or db_url.startswith("postgresql://")


def get_conn():
    """Retorna uma conexão compatível com SQLite ou PostgreSQL (com pool de conexões)."""
    db_url = os.environ.get("DATABASE_URL", "").strip()

    if db_url.startswith("postgres://") or db_url.startswith("postgresql://"):
        import psycopg
        from psycopg.rows import dict_row

        # Corrige URL antiga postgres:// para postgresql:// exigida por drivers modernos
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        with _PG_POOL_LOCK:
            while _PG_POOL:
                pooled_url, raw_conn = _PG_POOL.pop()
                if pooled_url == db_url and not raw_conn.closed and not raw_conn.broken:
                    try:
                        # Ping rápido para garantir que a conexão não caiu por idle timeout na Neon
                        raw_conn.execute("SELECT 1")
                        return PostgresConnectionWrapper(raw_conn, pool_url=db_url)
                    except Exception:
                        try:
                            raw_conn.close()
                        except Exception:
                            pass
                else:
                    try:
                        raw_conn.close()
                    except Exception:
                        pass

        raw = psycopg.connect(db_url, row_factory=dict_row, autocommit=True)
        return PostgresConnectionWrapper(raw, pool_url=db_url)

    # Fallback: SQLite local
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Inicializa as tabelas no banco de dados ativo (Postgres ou SQLite)."""
    target = _current_db_target()
    with _SETTINGS_LOCK:
        _SETTINGS_CACHE.clear()

    if is_postgres():
        conn = get_conn()
        try:
            with conn:
                with conn._raw.cursor() as cur:
                    cur.execute(POSTGRES_SCHEMA)
                    cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS last_alert_at TEXT")
                    cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS last_alert_price DOUBLE PRECISION")
        finally:
            conn.close()
        with _INIT_LOCK:
            _INITIALIZED_TARGETS.add(target)
        return

    # SQLite
    conn = get_conn()
    try:
        with conn:
            conn.executescript(SQLITE_SCHEMA)
            prod_cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
            if "category" not in prod_cols:
                conn.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'Notebook'")
            if "last_alert_at" not in prod_cols:
                conn.execute("ALTER TABLE products ADD COLUMN last_alert_at TEXT")
            if "last_alert_price" not in prod_cols:
                conn.execute("ALTER TABLE products ADD COLUMN last_alert_price REAL")

            price_cols = {row["name"] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
            if "coupon_code" not in price_cols:
                conn.execute("ALTER TABLE prices ADD COLUMN coupon_code TEXT")
            if "final_price" not in price_cols:
                conn.execute("ALTER TABLE prices ADD COLUMN final_price REAL")
    finally:
        conn.close()
    with _INIT_LOCK:
        _INITIALIZED_TARGETS.add(target)


def ensure_db_initialized() -> None:
    """Inicializa o banco apenas se ainda não tiver sido inicializado para o target atual."""
    target = _current_db_target()
    with _INIT_LOCK:
        if target in _INITIALIZED_TARGETS:
            return
    init_db()


def get_setting(key: str, default: str | None = None) -> str | None:
    """Busca um valor de configuração persistente na tabela app_settings (com cache em memória)."""
    cache_key = f"{_current_db_target()}::{key}"
    now = time.monotonic()
    with _SETTINGS_LOCK:
        cached = _SETTINGS_CACHE.get(cache_key)
        if cached is not None and (now - cached[1]) < _SETTINGS_TTL:
            return cached[0] if cached[0] is not None else default

    conn = get_conn()
    try:
        cur = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cur.fetchone()
        val = (row["value"] if isinstance(row, dict) else row[0]) if row else None
        with _SETTINGS_LOCK:
            _SETTINGS_CACHE[cache_key] = (val, now)
        return val if val is not None else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    """Salva ou atualiza um valor de configuração na tabela app_settings."""
    set_settings_bulk({key: value})


def set_settings_bulk(items: dict[str, str]) -> None:
    """Salva ou atualiza múltiplas chaves atomicamente em uma única transação/conexão."""
    if not items:
        return
    conn = get_conn()
    try:
        with conn:
            for key, value in items.items():
                conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
                    (key, value),
                )
        target = _current_db_target()
        now = time.monotonic()
        with _SETTINGS_LOCK:
            for key, value in items.items():
                _SETTINGS_CACHE[f"{target}::{key}"] = (value, now)
    finally:
        conn.close()

