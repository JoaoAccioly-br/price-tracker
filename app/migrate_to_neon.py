"""Script para migrar todos os dados do SQLite local (data/tracker.db) para o PostgreSQL da Neon.

Uso:
    python -m app.migrate_to_neon "postgresql://user:password@ep-xyz.us-east-2.aws.neon.tech/neondb?sslmode=require"

Ou defina DATABASE_URL no ambiente:
    export DATABASE_URL="postgresql://..."
    python -m app.migrate_to_neon
"""
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .db import POSTGRES_SCHEMA

SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"


def migrate(pg_url: str):
    if not SQLITE_PATH.exists():
        print(f"[-] Arquivo SQLite não encontrado em {SQLITE_PATH}. Nada para migrar.")
        return

    # Garante protocolo postgresql://
    if pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql://", 1)

    print(f"[*] Conectando ao SQLite local: {SQLITE_PATH}")
    s_conn = sqlite3.connect(SQLITE_PATH)
    s_conn.row_factory = sqlite3.Row

    print("[*] Conectando ao PostgreSQL (Neon)...")
    pg_conn = psycopg.connect(pg_url, row_factory=dict_row)

    try:
        # 1. Cria as tabelas se não existirem
        print("[*] Criando tabelas no PostgreSQL da Neon...")
        with pg_conn.cursor() as cur:
            cur.execute(POSTGRES_SCHEMA)
        pg_conn.commit()

        # 2. Migra produtos
        prods = s_conn.execute("SELECT id, name, category, target_price FROM products ORDER BY id").fetchall()
        print(f"[*] Migrando {len(prods)} produtos...")
        with pg_conn.cursor() as cur:
            for p in prods:
                cur.execute(
                    """INSERT INTO products (id, name, category, target_price)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE
                       SET name = EXCLUDED.name, category = EXCLUDED.category, target_price = EXCLUDED.target_price""",
                    (p["id"], p["name"], p["category"], p["target_price"]),
                )
            # Atualiza sequência de ID do Postgres
            cur.execute("SELECT setval('products_id_seq', COALESCE((SELECT MAX(id) FROM products), 1), true)")
        pg_conn.commit()

        # 3. Migra listings
        listings = s_conn.execute("SELECT id, product_id, store, url, css_selector, use_browser, active FROM listings ORDER BY id").fetchall()
        print(f"[*] Migrando {len(listings)} links/lojas...")
        with pg_conn.cursor() as cur:
            for l in listings:
                cur.execute(
                    """INSERT INTO listings (id, product_id, store, url, css_selector, use_browser, active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE
                       SET store = EXCLUDED.store, url = EXCLUDED.url, css_selector = EXCLUDED.css_selector,
                           use_browser = EXCLUDED.use_browser, active = EXCLUDED.active""",
                    (l["id"], l["product_id"], l["store"], l["url"], l["css_selector"], l["use_browser"], l["active"]),
                )
            cur.execute("SELECT setval('listings_id_seq', COALESCE((SELECT MAX(id) FROM listings), 1), true)")
        pg_conn.commit()

        # 4. Migra histórico de preços
        prices = s_conn.execute("SELECT id, listing_id, price, available, coupon_code, final_price, checked_at FROM prices ORDER BY id").fetchall()
        print(f"[*] Migrando {len(prices)} registros de preços do histórico...")
        with pg_conn.cursor() as cur:
            for pr in prices:
                cur.execute(
                    """INSERT INTO prices (id, listing_id, price, available, coupon_code, final_price, checked_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE
                       SET price = EXCLUDED.price, available = EXCLUDED.available, coupon_code = EXCLUDED.coupon_code,
                           final_price = EXCLUDED.final_price, checked_at = EXCLUDED.checked_at""",
                    (pr["id"], pr["listing_id"], pr["price"], pr["available"], pr["coupon_code"], pr["final_price"], pr["checked_at"]),
                )
            cur.execute("SELECT setval('prices_id_seq', COALESCE((SELECT MAX(id) FROM prices), 1), true)")
        pg_conn.commit()

        print("[+] Migração concluída com sucesso! Todos os dados já estão na Neon.")

    finally:
        s_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATABASE_URL")
    if not url:
        print("Erro: Forneça a Connection String da Neon como argumento ou defina DATABASE_URL.")
        print('Exemplo: python -m app.migrate_to_neon "postgresql://user:pass@ep-xyz.neon.tech/neondb?sslmode=require"')
        sys.exit(1)

    migrate(url)

