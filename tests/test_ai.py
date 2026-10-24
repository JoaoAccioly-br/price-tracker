import json
import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import (
    _call_gemini_generate_content,
    _diversify_by_store,
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


def test_should_auto_heal_ignores_transient_errors_and_triggers_on_real_expiration():
    """Testa se a detecção de auto-heal ignora erros transitórios e só aciona em links realmente expirados/esgotados."""
    import httpx
    from app.collector import _should_auto_heal

    # 1. NÃO deve acionar (False): Bloqueios temporários, falta de credenciais ou erros transitórios
    assert _should_auto_heal(PriceResult(price=None, available=False, source="amazon:captcha"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="shopee:no-credentials"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:no-token"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="aliexpress:csr-js-required"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="shopee:http-503"), None) is False

    # Outros bloqueios mapeados
    assert _should_auto_heal(PriceResult(price=None, available=False, source="amazon:price-hidden"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="shopee:invalid-url"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="shopee:http-429"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:sem-token"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:id-invalido"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:http-401"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:http-403"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:http-429"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:blocked"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="shopee:exception-Timeout"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:http-502"), None) is False
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:exception-RuntimeError"), None) is False

    # Timeout de rede ou exceção genérica
    assert _should_auto_heal(None, httpx.TimeoutException("timeout")) is False
    assert _should_auto_heal(None, ConnectionError("connection reset")) is False

    # HTTP 503 e 403 (erros de servidor / WAF / permissão)
    exc_503 = Exception("Service Unavailable")
    exc_503.response = MagicMock(status_code=503)
    assert _should_auto_heal(None, exc_503) is False

    exc_403 = Exception("Forbidden")
    exc_403.response = MagicMock(status_code=403)
    assert _should_auto_heal(None, exc_403) is False

    # Quando result é None sem exceção
    assert _should_auto_heal(None, None) is False

    # Produto ativo saudável não deve acionar heal
    assert _should_auto_heal(PriceResult(price=2500.0, available=True, source="amazon:#price"), None) is False

    # 2. DEVE acionar (True): Sinais reais de link expirado ou esgotado
    assert _should_auto_heal(PriceResult(price=None, available=False, source="shopee:not-found"), None) is True
    assert _should_auto_heal(PriceResult(price=None, available=False, source="not-found"), None) is True
    assert _should_auto_heal(PriceResult(price=3500.0, available=False, source="amazon:#price"), None) is True
    assert _should_auto_heal(PriceResult(price=None, available=False, source="ml:http-404"), None) is True

    # Exceção HTTP 404 (Not Found) ou 410 (Gone)
    exc_404 = Exception("Not Found")
    exc_404.response = MagicMock(status_code=404)
    assert _should_auto_heal(None, exc_404) is True

    exc_410 = Exception("Gone")
    exc_410.response = MagicMock(status_code=410)
    assert _should_auto_heal(None, exc_410) is True


def test_gemini_default_model(monkeypatch):
    """Testa se o modelo padrão agora é gemini-3.8-flash quando não configurado."""
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    import app.ai as ai
    ai._ACTIVE_GEMINI_MODEL = None
    assert get_gemini_model() == "gemini-3.8-flash"


def test_gemini_404_auto_fallback_and_caching(monkeypatch):
    """Testa auto-fallback quando a API retorna 404 no longer available to new users."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    import app.ai as ai
    ai._ACTIVE_GEMINI_MODEL = None

    called_urls = []

    def mock_post(url, **kwargs):
        called_urls.append(url)
        resp = MagicMock()
        if "gemini-2.5-flash:" in url:
            resp.status_code = 404
            resp.text = "404: This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash instead."
            return resp
        elif "gemini-3.8-flash:" in url:
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{
                    "groundingMetadata": {"groundingChunks": []},
                    "content": {"parts": [{"text": "https://www.amazon.com.br/dp/B012345678"}]}
                }]
            }
            return resp
        resp.status_code = 404
        resp.text = "Not found"
        return resp

    with patch("httpx.post", side_effect=mock_post):
        results = _gemini_grounded_search("Notebook Dell Inspiron", "Notebook")
        assert len(results) == 1
        assert "amazon.com.br" in results[0]["url"]

        # Verifica que tentou o modelo inicial e depois o modelo sugerido
        assert any("gemini-2.5-flash:" in u for u in called_urls)
        assert any("gemini-3.8-flash:" in u for u in called_urls)
        # Verifica que o modelo ativo em cache agora é o que funcionou
        assert ai._ACTIVE_GEMINI_MODEL == "gemini-3.8-flash"
        assert get_gemini_model() == "gemini-3.8-flash"

    # Segunda chamada já deve ir direto para o modelo funcional em cache
    called_urls.clear()
    with patch("httpx.post", side_effect=mock_post):
        results = _gemini_grounded_search("Notebook Dell Inspiron", "Notebook")
        assert len(results) == 1
        assert not any("gemini-2.5-flash:" in u for u in called_urls)
        assert any("gemini-3.8-flash:" in u for u in called_urls)


def test_gemini_404_auto_fallback_list_order(monkeypatch):
    """Testa fallback através da lista de modelos quando a mensagem de erro não sugere modelo."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom-old")
    import app.ai as ai
    ai._ACTIVE_GEMINI_MODEL = None

    called_models = []

    def mock_post(url, **kwargs):
        model = url.split("/models/")[1].split(":")[0]
        called_models.append(model)
        resp = MagicMock()
        if model == "gemini-custom-old":
            resp.status_code = 404
            resp.text = "Model not found"
            return resp
        if model == "gemini-3.8-flash":
            resp.status_code = 404
            resp.text = "Quota exceeded or not available"
            return resp
        if model == "gemini-2.5-flash-lite":
            resp.status_code = 404
            resp.text = "Quota exceeded or not available"
            return resp
        if model == "gemini-3-flash-preview":
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{
                    "content": {"parts": [{"text": '{"matches": []}'}]}
                }]
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.post", side_effect=mock_post):
        res = _call_gemini_generate_content(headers={}, payload={})
        assert res is not None
        assert res.status_code == 200
        assert called_models == ["gemini-custom-old", "gemini-3.8-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]
        assert ai._ACTIVE_GEMINI_MODEL == "gemini-3-flash-preview"


def test_diversify_by_store_round_robin():
    """Testa intercalação round-robin entre lojas garantindo que pelo menos 1 de cada loja apareça."""
    mock_results = [
        {"store": "Shopee", "title": "Shopee Item 1", "url": "https://shopee.com.br/item1"},
        {"store": "Shopee", "title": "Shopee Item 2", "url": "https://shopee.com.br/item2"},
        {"store": "Shopee", "title": "Shopee Item 3", "url": "https://shopee.com.br/item3"},
        {"store": "Shopee", "title": "Shopee Item 4", "url": "https://shopee.com.br/item4"},
        {"store": "Shopee", "title": "Shopee Item 5", "url": "https://shopee.com.br/item5"},
        {"store": "Mercado Livre", "title": "ML Item 1", "url": "https://mercadolivre.com.br/p/MLB1"},
        {"store": "Mercado Livre", "title": "ML Item 2", "url": "https://mercadolivre.com.br/p/MLB2"},
        {"store": "Amazon", "title": "Amazon Item 1", "url": "https://amazon.com.br/dp/A1"},
    ]

    diversified = _diversify_by_store(mock_results, max_results=6)
    assert len(diversified) == 6

    # Na 1ª rodada (primeiros 3 itens), deve haver exatamente 1 de cada uma das 3 lojas
    first_three_stores = [item["store"] for item in diversified[:3]]
    assert first_three_stores == ["Shopee", "Mercado Livre", "Amazon"]

    # Na 2ª rodada, deve pegar o 2º item de Shopee e ML
    next_two_stores = [item["store"] for item in diversified[3:5]]
    assert next_two_stores == ["Shopee", "Mercado Livre"]

    # Na 3ª rodada, pega o 3º item da Shopee
    assert diversified[5]["store"] == "Shopee"
    assert diversified[5]["title"] == "Shopee Item 3"


def test_discover_product_links_diversifies_stores_in_result(monkeypatch):
    """Testa que discover_product_links aplica a diversificação por loja no resultado final."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    shopee_items = [
        {"store": "Shopee", "title": f"Notebook Lenovo IdeaPad {i}", "url": f"https://shopee.com.br/item{i}", "price": 2000.0 + i, "source": "shopee:api"}
        for i in range(1, 6)
    ]
    ml_items = [
        {"store": "Mercado Livre", "title": f"Notebook Lenovo IdeaPad ML {i}", "url": f"https://mercadolivre.com.br/p/MLB{i}", "price": 2100.0 + i, "source": "ml:api"}
        for i in range(1, 3)
    ]

    with patch("app.ai.search_shopee_offers", return_value=shopee_items), \
         patch("app.ai.search_mercadolivre_offers", return_value=ml_items):

        res = discover_product_links("Notebook Lenovo IdeaPad", max_results=6)
        results = res["results"]
        assert len(results) == 6

        # Garante que as lojas foram intercaladas em vez de 5 Shopee e 1 ML
        assert results[0]["store"] == "Shopee"
        assert results[1]["store"] == "Mercado Livre"
        assert results[2]["store"] == "Shopee"
        assert results[3]["store"] == "Mercado Livre"
        assert results[4]["store"] == "Shopee"
        assert results[5]["store"] == "Shopee"
