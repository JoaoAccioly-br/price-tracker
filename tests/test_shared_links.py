import os
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from app import db
from app.analyzer import get_product_analysis
from app.collector import collect_all
from app.main import app
from app.scrapers.base import PriceResult, canonicalize_url


@pytest.fixture()
def clean_db(tmp_path, monkeypatch):
    """Garante banco limpo para cada teste."""
    db_file = tmp_path / "test_shared.db"
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        monkeypatch.setattr(db, "DB_PATH", db_file)
        with db._INIT_LOCK:
            db._INITIALIZED_TARGETS.discard(f"sqlite://{db_file}")
    with db._SETTINGS_LOCK:
        db._SETTINGS_CACHE.clear()
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
    return db_file


@pytest.fixture()
def client(clean_db):
    with TestClient(app) as c:
        yield c


# ==============================================================================
# 1. Canonicalize URL Tests
# ==============================================================================
def test_canonicalize_url_shopee():
    # Formato com sufixo -i.{shop_id}.{item_id}
    url1 = "https://shopee.com.br/Produto-Legal-i.12345678.98765432?sp_atk=abc12345&xptdk=def"
    # Formato /product/{shop_id}/{item_id}
    url2 = "https://shopee.com.br/product/12345678/98765432"
    # Com subdomínio m.shopee.com.br
    url3 = "https://m.shopee.com.br/product/12345678/98765432?sp_atk=other"

    assert canonicalize_url(url1) == "shopee:12345678:98765432"
    assert canonicalize_url(url2) == "shopee:12345678:98765432"
    assert canonicalize_url(url3) == "shopee:12345678:98765432"


def test_canonicalize_url_mercadolivre():
    url_catalog = "https://www.mercadolivre.com.br/p/MLB12345678?pdp_filters=category:MLB123#reco_item"
    url_item = "https://produto.mercadolivre.com.br/MLB-12345678-notebook-gamer_JM?matt_tool=999"
    url_query = "https://mercadolivre.com.br/item?item_id=MLB12345678&tracking_id=xyz"

    key1 = canonicalize_url(url_catalog)
    key2 = canonicalize_url(url_item)
    key3 = canonicalize_url(url_query)

    assert "ml:MLB12345678" in key1
    assert "ml:MLB12345678" in key2
    assert "ml:MLB12345678" in key3


def test_canonicalize_url_amazon():
    url_dp = "https://www.amazon.com.br/dp/B09V3HN1KC/ref=sr_1_1?keywords=notebook&tag=afiliado"
    url_gp = "https://amazon.com.br/gp/product/B09V3HN1KC?ref_=nav_signin"
    url_us = "https://www.amazon.com/dp/b09v3hn1kc?ref=123"

    assert canonicalize_url(url_dp) == "amazon:B09V3HN1KC"
    assert canonicalize_url(url_gp) == "amazon:B09V3HN1KC"
    assert canonicalize_url(url_us) == "amazon:B09V3HN1KC"


def test_canonicalize_url_aliexpress():
    url_promo = "https://pt.aliexpress.com/item/1005006123456789.html?pdp_npi=4%40dis%21BRL%21100%2150&spm=a2g0o"
    url_plain = "https://www.aliexpress.com/item/1005006123456789.html"
    url_mobile = "https://m.aliexpress.com/item/1005006123456789.html?sk=abcd"

    assert canonicalize_url(url_promo) == "aliexpress:1005006123456789"
    assert canonicalize_url(url_plain) == "aliexpress:1005006123456789"
    assert canonicalize_url(url_mobile) == "aliexpress:1005006123456789"


def test_canonicalize_url_generic_tracking_stripping():
    url_kabum1 = "https://www.kabum.com.br/produto/12345/teclado-gamer/?utm_source=google&utm_medium=cpc&outro=1&fbclid=xyz"
    url_kabum2 = "https://www.kabum.com.br/produto/12345/teclado-gamer?outro=1&gclid=abc&ref=999"

    assert canonicalize_url(url_kabum1) == "https://www.kabum.com.br/produto/12345/teclado-gamer?outro=1"
    assert canonicalize_url(url_kabum2) == "https://www.kabum.com.br/produto/12345/teclado-gamer?outro=1"


# ==============================================================================
# 2. Duplicate Canonical Link Rejection (409)
# ==============================================================================
def test_duplicate_canonical_link_blocked_same_product(client):
    res_p = client.post("/api/products", json={"name": "Notebook Gamer", "target_price": 5000})
    assert res_p.status_code == 201
    pid = res_p.json()["id"]

    # Link 1 com parâmetros de campanha
    res1 = client.post("/api/listings", json={
        "product_id": pid,
        "store": "Amazon",
        "url": "https://www.amazon.com.br/dp/B09V3HN1KC?utm_source=google&ref=campanha1",
    })
    assert res1.status_code == 201

    # Link 2 para o mesmo produto com URL diferente mas mesmo ASIN -> deve retornar 409
    res2 = client.post("/api/listings", json={
        "product_id": pid,
        "store": "Amazon Brasil",
        "url": "https://www.amazon.com.br/gp/product/B09V3HN1KC?ref_=tag_afiliado",
    })
    assert res2.status_code == 409
    assert "já está cadastrado" in res2.json()["detail"]


# ==============================================================================
# 3. Two Accounts Shared Link & Immediate History Backfill
# ==============================================================================
def test_two_accounts_shared_link_immediate_history(clean_db):
    with TestClient(app) as client_a, TestClient(app) as client_b:
        # Registra Usuária A
        res_a = client_a.post("/api/auth/register", json={
            "name": "Alice", "email": "alice@teste.com", "password": "password123",
        })
        assert res_a.status_code == 201

        # Usuária A cadastra produto e listing da Amazon
        pa_res = client_a.post("/api/products", json={"name": "Dell XPS Alice", "target_price": 6000})
        pa_id = pa_res.json()["id"]
        la_res = client_a.post("/api/listings", json={
            "product_id": pa_id,
            "store": "Amazon",
            "url": "https://www.amazon.com.br/dp/B09V3HN1KC?tag=alice",
        })
        la_id = la_res.json()["id"]

        # Insere leituras históricas acumuladas para a listing da Alice
        conn = db.get_conn()
        with conn:
            conn.execute(
                "INSERT INTO prices (listing_id, price, available, checked_at) VALUES (?, 6500, 1, '2026-09-10 10:00:00')",
                (la_id,),
            )
            conn.execute(
                "INSERT INTO prices (listing_id, price, available, checked_at) VALUES (?, 6200, 1, '2026-09-20 10:00:00')",
                (la_id,),
            )
            conn.execute(
                "INSERT INTO prices (listing_id, price, available, checked_at) VALUES (?, 5800, 1, '2026-10-01 10:00:00')",
                (la_id,),
            )
            conn.execute(
                "INSERT INTO prices (listing_id, price, available, checked_at) VALUES (?, 5400, 1, '2026-10-15 10:00:00')",
                (la_id,),
            )
        conn.close()

        # Registra Usuário B
        res_b = client_b.post("/api/auth/register", json={
            "name": "Bob", "email": "bob@teste.com", "password": "password123",
        })
        assert res_b.status_code == 201

        # Usuário B cadastra o mesmo produto com URL equivalente (parâmetro de rastreio diferente)
        pb_res = client_b.post("/api/products", json={"name": "Dell XPS Bob", "target_price": 5500})
        pb_id = pb_res.json()["id"]
        lb_res = client_b.post("/api/listings", json={
            "product_id": pb_id,
            "store": "Amazon BR",
            "url": "https://www.amazon.com.br/gp/product/B09V3HN1KC?ref=bob_campanha",
        })
        assert lb_res.status_code == 201

        # 1. Usuário B deve ter imediatamente o best_price = 5400 na listagem
        prods_b = client_b.get("/api/products").json()
        assert len(prods_b) == 1
        assert prods_b[0]["id"] == pb_id
        assert prods_b[0]["best_price"] == 5400
        assert prods_b[0]["listings"][0]["last_price"] == 5400

        # 2. Usuário B deve ter imediatamente todo o histórico em /history
        hist_b = client_b.get(f"/api/products/{pb_id}/history").json()
        prices_b = [h["price"] for h in hist_b]
        assert prices_b == [6500, 6200, 5800, 5400]

        # 3. Usuário B deve ter imediatamente a análise de 30/60 dias em /analysis
        analysis_b = client_b.get(f"/api/products/{pb_id}/analysis").json()
        assert analysis_b["verdict"] in ("real_deal", "all_time_low")
        assert analysis_b["current_price"] == 5400
        assert analysis_b["min_30d"] == 5400
        assert analysis_b["days_tracked"] > 1


# ==============================================================================
# 4. Deduplicated Scrape & Individual Alert Dispatch
# ==============================================================================
def test_collect_all_deduplication_and_individual_alerts(clean_db, monkeypatch):
    with TestClient(app) as client_a, TestClient(app) as client_b:
        # Usuária A: target 300, chat_id "111111"
        client_a.post("/api/auth/register", json={
            "name": "Alice", "email": "a@teste.com", "password": "password123", "telegram_chat_id": "111111",
        })
        pa_id = client_a.post("/api/products", json={"name": "Fone Promo", "target_price": 300.0}).json()["id"]
        # Alice cadastra sem pdp_npi
        client_a.post("/api/listings", json={
            "product_id": pa_id,
            "store": "AliExpress",
            "url": "https://pt.aliexpress.com/item/100500123456789.html",
        })

        # Usuário B: target 200, chat_id "222222"
        client_b.post("/api/auth/register", json={
            "name": "Bob", "email": "b@teste.com", "password": "password123", "telegram_chat_id": "222222",
        })
        pb_id = client_b.post("/api/products", json={"name": "Fone Bob", "target_price": 200.0}).json()["id"]
        # Bob cadastra COM pdp_npi promocional
        client_b.post("/api/listings", json={
            "product_id": pb_id,
            "store": "AliExpress BR",
            "url": "https://pt.aliexpress.com/item/100500123456789.html?pdp_npi=4%40dis%21BRL%21350.00%21250.00",
        })

        # Mock de scrape para validar que foi chamado apenas 1 vez e com a melhor URL (com pdp_npi=)
        scrape_mock = MagicMock(return_value=PriceResult(
            price=350.0,
            final_price=250.0,
            available=True,
            source="aliexpress:url-npi",
        ))
        monkeypatch.setattr("app.collector.scrape", scrape_mock)

        # Mock de notify_price_drop para validar os alertas individuais por usuário
        notify_mock = MagicMock()
        monkeypatch.setattr("app.collector.notify_price_drop", notify_mock)

        # Executa coleta
        stats = collect_all(delay=(0.0, 0.0))
        assert stats["ok"] == 2
        assert stats["failed"] == 0

        # O scraper HTTP foi chamado APENAS UMA VEZ
        assert scrape_mock.call_count == 1
        # E foi chamado com a URL que tem pdp_npi=
        called_url = scrape_mock.call_args[0][0]
        assert "pdp_npi=" in called_url

        # Ambas as contas tiveram a leitura gravada no banco
        hist_a = client_a.get(f"/api/products/{pa_id}/history").json()
        hist_b = client_b.get(f"/api/products/{pb_id}/history").json()
        assert len(hist_a) == 1
        assert len(hist_b) == 1
        assert hist_a[0]["final_price"] == 250.0
        assert hist_b[0]["final_price"] == 250.0
        # O timestamp checked_at é idêntico em ambas
        assert hist_a[0]["checked_at"] == hist_b[0]["checked_at"]

        # Avaliação de alertas individuais:
        # Preço final obtido = 250.0
        # Alice tinha target = 300.0 -> 250.0 <= 300.0 -> ALERTA DISPARADO para chat_id "111111"
        # Bob tinha target = 200.0 -> 250.0 > 200.0 -> SEM ALERTA para Bob
        assert notify_mock.call_count == 1
        call_kwargs = notify_mock.call_args[1]
        assert call_kwargs["telegram_chat_id"] == "111111"
        assert call_kwargs["target_price"] == 300.0
        assert call_kwargs["current_price"] == 250.0


# ==============================================================================
# 5. History Preservation & Deletion Lifecycle
# ==============================================================================
def test_history_preservation_and_deletion_lifecycle(clean_db):
    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.post("/api/auth/register", json={"name": "Alice", "email": "a@life.com", "password": "password123"})
        pa_id = client_a.post("/api/products", json={"name": "Monitor Alice", "target_price": 1000}).json()["id"]
        la_id = client_a.post("/api/listings", json={
            "product_id": pa_id, "store": "Kabum", "url": "https://www.kabum.com.br/produto/999/monitor-gamer",
        }).json()["id"]

        # Alice acumula leituras
        conn = db.get_conn()
        with conn:
            conn.execute("INSERT INTO prices (listing_id, price, checked_at) VALUES (?, 1200, '2026-10-01 12:00:00')", (la_id,))
            conn.execute("INSERT INTO prices (listing_id, price, checked_at) VALUES (?, 1100, '2026-10-02 12:00:00')", (la_id,))
        conn.close()

        # Bob cadastra o mesmo monitor
        client_b.post("/api/auth/register", json={"name": "Bob", "email": "b@life.com", "password": "password123"})
        pb_id = client_b.post("/api/products", json={"name": "Monitor Bob", "target_price": 1000}).json()["id"]
        lb_id = client_b.post("/api/listings", json={
            "product_id": pb_id, "store": "Kabum", "url": "https://www.kabum.com.br/produto/999/monitor-gamer?utm_source=twitter",
        }).json()["id"]

        # Bob recebe as 2 leituras no backfill
        hist_b = client_b.get(f"/api/products/{pb_id}/history").json()
        assert len(hist_b) == 2

        # Inserimos uma leitura exclusiva na listing de Alice que Bob ainda não tinha
        conn = db.get_conn()
        with conn:
            conn.execute("INSERT INTO prices (listing_id, price, checked_at) VALUES (?, 950, '2026-10-03 12:00:00')", (la_id,))
        conn.close()

        # Agora Alice exclui o produto dela
        del_res = client_a.delete(f"/api/products/{pa_id}")
        assert del_res.status_code == 200

        # Bob NÃO deve perder o histórico! Inclusive a leitura 950 foi preservada para Bob antes do delete
        hist_b_after = client_b.get(f"/api/products/{pb_id}/history").json()
        prices_after = [h["price"] for h in hist_b_after]
        assert prices_after == [1200, 1100, 950]

        # Agora Bob (a última referência ao link) exclui seu produto
        del_b_res = client_b.delete(f"/api/products/{pb_id}")
        assert del_b_res.status_code == 200

        # Não restou nenhum listing com essa canonical_key, portanto a tabela prices não deve reter nenhum registro
        conn = db.get_conn()
        remaining_prices = conn.execute("SELECT COUNT(*) AS total FROM prices").fetchone()["total"]
        conn.close()
        assert remaining_prices == 0


def test_init_db_backfill_existing_listings(clean_db):
    """Garante que init_db preenche canonical_key em listings legadas que estavam com campo vazio/nulo."""
    conn = db.get_conn()
    with conn:
        pid = conn.execute("INSERT INTO products (name) VALUES ('Legado')").lastrowid
        conn.execute(
            "INSERT INTO listings (product_id, store, url, canonical_key) VALUES (?, 'Amazon', 'https://www.amazon.com.br/dp/B08N5WRWNW', NULL)",
            (pid,),
        )
    conn.close()

    # Re-executa init_db
    db.init_db()

    conn = db.get_conn()
    row = conn.execute("SELECT canonical_key FROM listings WHERE product_id = ?", (pid,)).fetchone()
    conn.close()
    assert row["canonical_key"] == "amazon:B08N5WRWNW"


def test_sibling_live_sync_and_different_store_preservation(clean_db):
    """Testa leitura sincronizada ao vivo entre listings irmãos e preservação de lojas diferentes no mesmo timestamp."""
    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.post("/api/auth/register", json={"name": "Alice", "email": "alice_sync@teste.com", "password": "password123"})
        pa_id = client_a.post("/api/products", json={"name": "Monitor Gamer", "target_price": 1000}).json()["id"]
        la_amazon = client_a.post("/api/listings", json={
            "product_id": pa_id, "store": "Amazon", "url": "https://www.amazon.com.br/dp/B09V3HN1KC?ref=alice",
        }).json()["id"]

        client_b.post("/api/auth/register", json={"name": "Bob", "email": "bob_sync@teste.com", "password": "password123"})
        pb_id = client_b.post("/api/products", json={"name": "Monitor Gamer Bob", "target_price": 1000}).json()["id"]
        lb_amazon = client_b.post("/api/listings", json={
            "product_id": pb_id, "store": "Amazon BR", "url": "https://www.amazon.com.br/gp/product/B09V3HN1KC?ref=bob",
        }).json()["id"]
        lb_kabum = client_b.post("/api/listings", json={
            "product_id": pb_id, "store": "Kabum", "url": "https://www.kabum.com.br/produto/555/monitor",
        }).json()["id"]

        # Insere leitura em la_amazon DIRETAMENTE no banco (sem que lb_amazon tenha recebido)
        conn = db.get_conn()
        with conn:
            conn.execute(
                "INSERT INTO prices (listing_id, price, available, checked_at) VALUES (?, 1400, 1, '2026-10-20 10:00:00')",
                (la_amazon,),
            )
            # Insere leitura no mesmo timestamp para Kabum no produto de Bob
            conn.execute(
                "INSERT INTO prices (listing_id, price, available, checked_at) VALUES (?, 1350, 1, '2026-10-20 10:00:00')",
                (lb_kabum,),
            )
        conn.close()

        # Bob deve ver em sua listagem a leitura sincronizada do irmão
        prods_b = client_b.get("/api/products").json()
        assert prods_b[0]["best_price"] == 1350  # Kabum 1350 < Amazon 1400

        # No histórico do produto de Bob, ambos os links (Amazon e Kabum) no mesmo timestamp 10:00:00 devem aparecer!
        hist_b = client_b.get(f"/api/products/{pb_id}/history").json()
        stores = [h["store"] for h in hist_b]
        assert "Amazon BR" in stores
        assert "Kabum" in stores
        prices = [h["price"] for h in hist_b]
        assert 1400 in prices
        assert 1350 in prices


def test_delete_listing_preserves_shared_history(clean_db):
    """Testa exclusão via DELETE /api/listings/{id} garantindo preservação de histórico."""
    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.post("/api/auth/register", json={"name": "Alice", "email": "a_del@teste.com", "password": "password123"})
        pa_id = client_a.post("/api/products", json={"name": "Headset", "target_price": 500}).json()["id"]
        la_id = client_a.post("/api/listings", json={
            "product_id": pa_id, "store": "Amazon", "url": "https://www.amazon.com.br/dp/B012345678",
        }).json()["id"]

        conn = db.get_conn()
        with conn:
            conn.execute("INSERT INTO prices (listing_id, price, checked_at) VALUES (?, 450, '2026-10-10 10:00:00')", (la_id,))
        conn.close()

        client_b.post("/api/auth/register", json={"name": "Bob", "email": "b_del@teste.com", "password": "password123"})
        pb_id = client_b.post("/api/products", json={"name": "Headset Bob", "target_price": 500}).json()["id"]
        lb_id = client_b.post("/api/listings", json={
            "product_id": pb_id, "store": "Amazon BR", "url": "https://www.amazon.com.br/gp/product/B012345678",
        }).json()["id"]

        # Alice exclui apenas a listing (mantendo o produto dela)
        del_res = client_a.delete(f"/api/listings/{la_id}")
        assert del_res.status_code == 200

        # Bob mantém o histórico
        hist_b = client_b.get(f"/api/products/{pb_id}/history").json()
        assert len(hist_b) == 1
        assert hist_b[0]["price"] == 450

        # Bob agora exclui a listing dele
        del_b_res = client_b.delete(f"/api/listings/{lb_id}")
        assert del_b_res.status_code == 200

        # Sem mais referências, os preços devem ser removidos
        conn = db.get_conn()
        count = conn.execute("SELECT COUNT(*) AS c FROM prices").fetchone()["c"]
        conn.close()
        assert count == 0
