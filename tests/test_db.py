import os
import pytest
from app import db


def test_db_settings_and_transactions(tmp_path, monkeypatch):
    """Testa inicialização, inserções em app_settings e transações com with conn:."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_db.db")

    db.init_db()
    if db_url:
        conn = db.get_conn()
        with conn:
            conn.execute("DELETE FROM prices")
            conn.execute("DELETE FROM listings")
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM app_settings")
        conn.close()

    # 1. Testar get_setting e set_setting (garante que tabela sem coluna 'id' funciona)
    db.set_setting("test_key", "test_value_1")
    assert db.get_setting("test_key") == "test_value_1"

    # Atualização com ON CONFLICT
    db.set_setting("test_key", "test_value_2")
    assert db.get_setting("test_key") == "test_value_2"
    assert db.get_setting("non_existent_key") is None

    # 2. Testar inserções com with conn: (garante que o context manager fecha e comita sem erro)
    conn = db.get_conn()
    with conn:
        cur = conn.execute("INSERT INTO products (name, category, target_price) VALUES (?, ?, ?)", ("Teste", "Periférico", 100.0))
        prod_id = cur.lastrowid
        cur_l = conn.execute("INSERT INTO listings (product_id, store, url) VALUES (?, ?, ?)", (prod_id, "Loja Teste", "https://teste.com/prod"))
        list_id = cur_l.lastrowid

        # Inserção de preço (testa se o with conn: comita sem conflito com o driver)
        conn.execute(
            "INSERT INTO prices (listing_id, price, available, final_price) VALUES (?, ?, ?, ?)",
            (list_id, 99.90, 1, 99.90),
        )
    conn.close()

    # 3. Verificar se os dados foram persistidos
    conn2 = db.get_conn()
    row = conn2.execute("SELECT * FROM prices WHERE listing_id = ?", (list_id,)).fetchone()
    conn2.close()

    assert row is not None
    price_val = row["price"] if isinstance(row, dict) else row[2]
    assert float(price_val) == 99.90

    # Limpeza pós-teste
    conn3 = db.get_conn()
    with conn3:
        conn3.execute("DELETE FROM prices WHERE listing_id = ?", (list_id,))
        conn3.execute("DELETE FROM listings WHERE id = ?", (list_id,))
        conn3.execute("DELETE FROM products WHERE id = ?", (prod_id,))
        conn3.execute("DELETE FROM app_settings WHERE key = ?", ("test_key",))
    conn3.close()


def test_postgres_prepare_sql_preserves_question_marks_in_literals():
    wrapper = db.PostgresConnectionWrapper(raw_conn=None)
    sql, needs_ret = wrapper._prepare_sql("SELECT * FROM listings WHERE url = 'https://loja.com/item?id=1' AND id = ?")
    assert needs_ret is False
    assert sql == "SELECT * FROM listings WHERE url = 'https://loja.com/item?id=1' AND id = %s"

    # INSERT em products COM menção a app_settings em literal ainda deve receber RETURNING id
    sql_ins, needs_ret_ins = wrapper._prepare_sql("INSERT INTO products (name) VALUES ('app_settings')")
    assert needs_ret_ins is True
    assert sql_ins.endswith("RETURNING id")


def test_postgres_init_db_migration_when_listings_table_already_exists(monkeypatch):
    """Garante que POSTGRES_SCHEMA não cria índice em canonical_key antes do ALTER TABLE,
    e que init_db() em PostgreSQL migra tabelas legadas sem erro UndefinedColumn.
    """
    # 1. Verifica que os schemas não criam índice em canonical_key prematuramente
    assert "idx_listings_canonical" not in db.POSTGRES_SCHEMA
    assert "idx_listings_canonical" not in db.SQLITE_SCHEMA

    # 2. Simula banco PostgreSQL existente onde 'listings' já existia sem 'canonical_key'
    monkeypatch.setenv("DATABASE_URL", "postgresql://mock_user:mock_pass@neon.tech/mock_db")

    executed_statements: list[str] = []
    state = {
        "canonical_key_column_added": False,
        "closed": False,
    }

    class MockCursor:
        def execute(self, sql: str, params=None):
            executed_statements.append(sql)
            # Se tentar criar índice em canonical_key antes do ALTER TABLE correspondente, falha como o Postgres real
            if "idx_listings_canonical" in sql or "(canonical_key)" in sql:
                if not state["canonical_key_column_added"]:
                    raise Exception('UndefinedColumn: column "canonical_key" does not exist')
            if "ALTER TABLE listings ADD COLUMN" in sql and "canonical_key" in sql:
                state["canonical_key_column_added"] = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockQueryResult:
        def fetchall(self):
            return [{"id": 1, "url": "https://www.mercadolivre.com.br/item-p/MLB123"}]

    class MockRawConn:
        def cursor(self):
            return MockCursor()

    class MockConn:
        def __init__(self):
            self._raw = MockRawConn()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def execute(self, sql: str, params=None):
            executed_statements.append(sql)
            if "SELECT id, url FROM listings" in sql:
                if not state["canonical_key_column_added"]:
                    raise Exception('UndefinedColumn: column "canonical_key" does not exist')
                return MockQueryResult()
            return None

        def close(self):
            state["closed"] = True

    mock_conn = MockConn()
    monkeypatch.setattr(db, "get_conn", lambda: mock_conn)

    # Executa init_db no fluxo PostgreSQL
    db.init_db()

    assert state["canonical_key_column_added"] is True
    assert state["closed"] is True
    # Garante a ordem: POSTGRES_SCHEMA -> ALTER TABLE canonical_key -> CREATE INDEX idx_listings_canonical
    schema_idx = executed_statements.index(db.POSTGRES_SCHEMA)
    alter_idx = next(i for i, s in enumerate(executed_statements) if "ALTER TABLE listings ADD COLUMN" in s and "canonical_key" in s)
    index_idx = next(i for i, s in enumerate(executed_statements) if "CREATE INDEX IF NOT EXISTS idx_listings_canonical" in s)

    assert schema_idx < alter_idx < index_idx

