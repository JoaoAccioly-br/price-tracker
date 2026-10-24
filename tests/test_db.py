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
