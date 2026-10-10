"""Uso:
    python -m app.cli add-product "Acer Nitro V15" --target 4500
    python -m app.cli add-listing 1 kabum "https://www.kabum.com.br/produto/..."
    python -m app.cli list
    python -m app.cli collect
"""
import argparse
import logging

from .collector import collect_all
from .db import get_conn, init_db


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()

    ap = argparse.ArgumentParser(prog="tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-product")
    p.add_argument("name")
    p.add_argument("--target", type=float, help="preço alvo para alerta")

    p = sub.add_parser("add-listing")
    p.add_argument("product_id", type=int)
    p.add_argument("store")
    p.add_argument("url")
    p.add_argument("--selector", help="seletor CSS do preço (plano B)")
    p.add_argument("--browser", action="store_true", help="usar Playwright")

    sub.add_parser("list")
    sub.add_parser("collect")

    args = ap.parse_args()
    conn = get_conn()

    if args.cmd == "add-product":
        with conn:
            cur = conn.execute(
                "INSERT INTO products (name, target_price) VALUES (?, ?)", (args.name, args.target)
            )
        print(f"Produto #{cur.lastrowid} criado.")
    elif args.cmd == "add-listing":
        with conn:
            cur = conn.execute(
                "INSERT INTO listings (product_id, store, url, css_selector, use_browser) "
                "VALUES (?, ?, ?, ?, ?)",
                (args.product_id, args.store, args.url, args.selector, int(args.browser)),
            )
        print(f"Link #{cur.lastrowid} criado.")
    elif args.cmd == "list":
        for r in conn.execute(
            "SELECT p.id, p.name, l.id AS lid, l.store, l.url FROM products p "
            "LEFT JOIN listings l ON l.product_id = p.id ORDER BY p.id, l.id"
        ):
            print(f"#{r['id']} {r['name']}  |  link #{r['lid']} {r['store']} {r['url']}")
    elif args.cmd == "collect":
        print(collect_all())


if __name__ == "__main__":
    main()
