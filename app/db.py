"""Banco SQLite: simples, sem servidor, suficiente para uso pessoal."""
import os
import sqlite3
from pathlib import Path

# Pode ser sobrescrito por variável de ambiente (útil no Docker / testes)
DB_PATH = Path(
    os.environ.get("TRACKER_DB_PATH")
    or Path(__file__).resolve().parent.parent / "data" / "tracker.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'Notebook', -- Notebook, Monitor, Periférico, etc.
    target_price REAL                              -- avisa quando o preço ficar <= isso
);

-- Um "listing" é o produto em uma loja específica (uma URL).
CREATE TABLE IF NOT EXISTS listings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store        TEXT NOT NULL,
    url          TEXT NOT NULL,
    css_selector TEXT,                    -- plano B se a página não tiver JSON-LD
    use_browser  INTEGER NOT NULL DEFAULT 0,  -- 1 = usar Playwright (site com JS)
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (product_id, url)
);

-- Histórico: uma linha por leitura.
CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price       REAL NOT NULL,
    available   INTEGER NOT NULL DEFAULT 1,
    coupon_code TEXT,                     -- código do cupom ativo (se houver)
    final_price REAL,                     -- preço com cupom aplicado
    checked_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prices_listing_time ON prices (listing_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_listings_product ON listings (product_id, active);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout: espera até 10s por um lock em vez de falhar com "database is locked"
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")  # seguro com WAL e muito mais rápido que FULL
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    with conn:
        conn.executescript(SCHEMA)
        # Migração transparente para bancos existentes
        prod_cols = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if "category" not in prod_cols:
            conn.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'Notebook'")

        price_cols = {row["name"] for row in conn.execute("PRAGMA table_info(prices)").fetchall()}
        if "coupon_code" not in price_cols:
            conn.execute("ALTER TABLE prices ADD COLUMN coupon_code TEXT")
        if "final_price" not in price_cols:
            conn.execute("ALTER TABLE prices ADD COLUMN final_price REAL")
    conn.close()
