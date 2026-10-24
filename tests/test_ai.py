import json
import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import (
    _gemini_filter_and_rank,
    _gemini_grounded_search,
    discover_product_links,
    get_gemini_api_key,
    get_gemini_model,
    heal_expired_listing,
)
from app.main import app
from app.scrapers.base import PriceResult


@pytest.fixture()
def clean_db(tmp_path, monkeypatch):
    """Garante banco limpo para cada teste."""
    db_file = tmp_path / "test_ai.db"
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


def test_gemini_config_and_grounded_search(monkeypatch):
    """Testa leitura de variáveis de ambiente e busca com Grounding mockada."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key_123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")

    assert get_gemini_api_key() == "test_gemini_key_123"
    assert get_gemini_model() == "gemini-2.5-pro"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://www.amazon.com.br/dp/B0CX234567",
                                "title": "Notebook Acer Aspire 5 Intel Core i5 8GB 256GB SSD",
                            }
                        }
                    ]
                },
                "content": {
                    "parts": [
                        {
                            "text": "Aqui está a oferta na KaBuM: https://www.kabum.com.br/produto/998877/notebook-acer-aspire-5"
                        }
                    ]
                },
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        results = _gemini_grounded_search("Notebook Acer Aspire 5", "Notebook")
        assert len(results) == 2
        stores = {r["store"] for r in results}
        assert "Amazon" in stores
        assert "KaBuM!" in stores
        mock_post.assert_called_once()


def test_gemini_filter_and_rank_with_ai(monkeypatch):
    """Testa curadoria com Gemini descartando acessórios e ranqueando produtos."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    candidates = [
        {"store": "Loja A", "title": "Fonte Carregador 65W para Dell Inspiron", "url": "https://loja.com/fonte", "price": 89.90},
        {"store": "Dell", "title": "Notebook Dell Inspiron 15 Core i5 16GB 512GB", "url": "https://dell.com/inspiron15", "price": 3499.00},
        {"store": "Loja B", "title": "Capa Case Neoprene para Dell Inspiron", "url": "https://loja.com/capa", "price": 49.90},
    ]

    mock_ai_resp = MagicMock()
    mock_ai_resp.status_code = 200
    mock_ai_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "matches": [
                                    {"index": 1, "confidence": "alta", "reason": "Notebook completo correto"}
                                ]
                            })
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_ai_resp):
        filtered = _gemini_filter_and_rank(candidates, "Notebook Dell Inspiron 15", "Notebook")
        assert len(filtered) == 1
        assert filtered[0]["title"] == "Notebook Dell Inspiron 15 Core i5 16GB 512GB"
        assert filtered[0]["confidence"] == "alta"
        assert filtered[0]["reason"] == "Notebook completo correto"


def test_gemini_filter_heuristic_fallback_when_no_api_key(monkeypatch):
    """Testa fallback heurístico descartando acessórios quando não há chave de API."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    candidates = [
        {"store": "Shopee", "title": "Fonte Carregador Compatível com Lenovo IdeaPad", "url": "https://shopee.com.br/fonte", "price": 50.0},
        {"store": "Shopee", "title": "Notebook Lenovo IdeaPad 3 Ryzen 5 8GB 256GB", "url": "https://shopee.com.br/ideapad", "price": 2499.0},
        {"store": "ML", "title": "Película Protetora de Teclado Lenovo", "url": "https://ml.com/pelicula", "price": 25.0},
    ]

    filtered = _gemini_filter_and_rank(candidates, "Notebook Lenovo IdeaPad 3", "Notebook")
    assert len(filtered) == 1
    assert "Notebook Lenovo IdeaPad 3" in filtered[0]["title"]
    assert filtered[0]["confidence"] == "media"


def test_discover_product_links_combining_stores_and_excluding(monkeypatch):
    """Testa descoberta unificada integrando Shopee e Mercado Livre e excluindo URLs já conhecidas."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    mock_shopee = [
        {"store": "Shopee", "title": "Notebook Acer Aspire 5 A515", "url": "https://shopee.com.br/item-acer", "price": 2899.0, "source": "shopee:api"},
        {"store": "Shopee", "title": "Capa Protetora Acer", "url": "https://shopee.com.br/capa-acer", "price": 39.0, "source": "shopee:api"},
    ]
    mock_ml = [
        {"store": "Mercado Livre", "title": "Notebook Acer Aspire 5 Core i5", "url": "https://mercadolivre.com.br/p/MLB112233", "price": 2950.0, "source": "ml:api"},
    ]

    with patch("app.ai.search_shopee_offers", return_value=mock_shopee), \
         patch("app.ai.search_mercadolivre_offers", return_value=mock_ml):

        # Teste com exclude_urls excluindo o link da Shopee
        res = discover_product_links(
            product_name="Notebook Acer Aspire 5",
            category="Notebook",
            exclude_urls=["https://shopee.com.br/item-acer"],
        )

        assert res["gemini_configured"] is False
        results = res["results"]
        urls = [r["url"] for r in results]

        # O link excluído não deve constar
        assert "https://shopee.com.br/item-acer" not in urls
        # O acessório foi descartado pela heurística
        assert "https://shopee.com.br/capa-acer" not in urls
        # O link do Mercado Livre deve estar presente
        assert "https://mercadolivre.com.br/p/MLB112233" in urls


def test_api_discover_links_endpoints(client):
    """Testa o endpoint POST /api/ai/discover-links por product_name e por product_id."""
    # 1. Cria produto e listing prévio
    prod_id = client.post("/api/products", json={"name": "Galaxy Book 3", "category": "Notebook"}).json()["id"]
    client.post("/api/listings", json={"product_id": prod_id, "store": "Amazon", "url": "https://amazon.com.br/galaxybook"})

    mock_cands = [
        {"store": "Mercado Livre", "title": "Samsung Galaxy Book 3 Core i5", "url": "https://mercadolivre.com.br/p/galaxy", "price": 3199.0, "source": "ml:api"},
        {"store": "Amazon", "title": "Samsung Galaxy Book 3 i5 8GB", "url": "https://amazon.com.br/galaxybook", "price": 3299.0, "source": "amazon"},
    ]

    with patch("app.ai.discover_product_links") as mock_disc:
        mock_disc.return_value = {"ai_used": False, "gemini_configured": False, "query": "Galaxy Book 3", "results": [mock_cands[0]]}

        # Chamada passando product_id
        res = client.post("/api/ai/discover-links", json={"product_id": prod_id})
        assert res.status_code == 200
        mock_disc.assert_called_once()
        _, kwargs = mock_disc.call_args
        assert kwargs["product_name"] == "Galaxy Book 3"
        # Garante que a URL prévia foi adicionada em exclude_urls
        assert "https://amazon.com.br/galaxybook" in kwargs["exclude_urls"]

    # Chamada sem product_name e sem product_id -> 422
    assert client.post("/api/ai/discover-links", json={}).status_code == 422


def test_heal_expired_listing_during_collection(clean_db):
    """Testa substituição automática de link expirado (Self-Healing) durante a coleta periódica."""
    conn = db.get_conn()
    with conn:
        cur_p = conn.execute("INSERT INTO products (name, category, target_price) VALUES ('Monitor LG 24', 'Monitor', 700.0)")
        p_id = cur_p.lastrowid
        cur_l = conn.execute("INSERT INTO listings (product_id, store, url, active) VALUES (?, 'Shopee', 'https://shopee.com.br/dead-link', 1)", (p_id,))
        l_id = cur_l.lastrowid
    conn.close()

    from app.collector import _collect_all

    # Simula que o link antigo retorna anúncio indisponível/esgotado
    dead_scrape = PriceResult(price=None, available=False, source="shopee:not-found")
    # Simula anúncio substituto ativo encontrado na mesma loja
    healed_scrape = PriceResult(price=649.90, available=True, source="shopee:api", final_price=649.90)

    def fake_scrape(url, selector=None):
        if "dead-link" in url:
            return dead_scrape
        if "healed-link" in url:
            return healed_scrape
        return PriceResult(price=None, available=False)

    fake_healed_link = ("https://shopee.com.br/healed-link", healed_scrape, "Monitor LG 24 Full HD Novo")

    with patch("app.collector.scrape", side_effect=fake_scrape), \
         patch("app.collector.heal_expired_listing", return_value=fake_healed_link) as mock_heal, \
         patch("app.collector.notify_price_drop") as mock_notify:

        stats = _collect_all(delay=(0, 0))

        # O auto-heal deve ter sido acionado para o link morto
        mock_heal.assert_called_once()
        assert stats["ok"] == 1
        assert stats["failed"] == 0

        # Verifica que o listing no banco foi atualizado com a nova URL
        conn = db.get_conn()
        l_row = conn.execute("SELECT url, canonical_key FROM listings WHERE id = ?", (l_id,)).fetchone()
        pr_row = conn.execute("SELECT price, available FROM prices WHERE listing_id = ?", (l_id,)).fetchone()
        conn.close()

        assert l_row["url"] == "https://shopee.com.br/healed-link"
        assert pr_row is not None
        assert pr_row["price"] == 649.90

        # O alerta de queda de preço deve ter sido disparado pois 649.90 <= 700.0 (alvo)
        assert mock_notify.call_count == 1


def test_manual_heal_endpoint(client):
    """Testa o endpoint manual POST /api/listings/{listing_id}/heal."""
    prod_id = client.post("/api/products", json={"name": "Teclado Mecânico", "category": "Periférico"}).json()["id"]
    list_id = client.post("/api/listings", json={"product_id": prod_id, "store": "KaBuM!", "url": "https://kabum.com.br/dead-item"}).json()["id"]

    healed_res = PriceResult(price=299.90, available=True, source="kabum", final_price=299.90)
    fake_healed = ("https://kabum.com.br/new-item-teclado", healed_res, "Teclado Mecânico RGB KaBuM")

    with patch("app.ai.heal_expired_listing", return_value=fake_healed):
        res = client.post(f"/api/listings/{list_id}/heal")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "substituido"
        assert data["old_url"] == "https://kabum.com.br/dead-item"
        assert data["new_url"] == "https://kabum.com.br/new-item-teclado"
        assert data["price"] == 299.90

    # Teste quando não há substituto -> 404
    with patch("app.ai.heal_expired_listing", return_value=(None, None, None)):
        res_fail = client.post(f"/api/listings/{list_id}/heal")
        assert res_fail.status_code == 404
