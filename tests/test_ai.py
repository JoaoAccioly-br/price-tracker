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
    _is_brazilian_store_url,
    _validate_and_enrich_candidates,
    discover_product_links,
    get_gemini_api_key,
    get_gemini_model,
    heal_expired_listing,
    search_amazon_offers,
    search_kabum_offers,
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
    """Testa se o modelo padrão agora é gemini-3.5-flash-lite quando não configurado."""
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    import app.ai as ai
    ai._ACTIVE_GEMINI_MODEL = None
    assert get_gemini_model() == "gemini-3.5-flash-lite"


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
            resp.text = "404: This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.5-flash-lite instead."
            return resp
        elif "gemini-3.5-flash-lite:" in url:
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
        assert any("gemini-3.5-flash-lite:" in u for u in called_urls)
        # Verifica que o modelo ativo em cache agora é o que funcionou
        assert ai._ACTIVE_GEMINI_MODEL == "gemini-3.5-flash-lite"
        assert get_gemini_model() == "gemini-3.5-flash-lite"

    # Segunda chamada já deve ir direto para o modelo funcional em cache
    called_urls.clear()
    with patch("httpx.post", side_effect=mock_post):
        results = _gemini_grounded_search("Notebook Dell Inspiron", "Notebook")
        assert len(results) == 1
        assert not any("gemini-2.5-flash:" in u for u in called_urls)
        assert any("gemini-3.5-flash-lite:" in u for u in called_urls)


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
        if model == "gemini-3.5-flash-lite":
            resp.status_code = 404
            resp.text = "Quota exceeded or not available"
            return resp
        if model == "gemini-3.5-flash-lite-preview":
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
        assert called_models == ["gemini-custom-old", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite-preview"]
        assert ai._ACTIVE_GEMINI_MODEL == "gemini-3.5-flash-lite-preview"


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


def test_gemini_429_auto_fallback_and_caching(monkeypatch):
    """Testa auto-fallback quando o modelo Gemini retorna HTTP 429 Quota Exceeded."""
    import httpx
    import app.ai as ai

    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    ai._ACTIVE_GEMINI_MODEL = None

    called_urls = []

    def mock_post(url, **kwargs):
        called_urls.append(url)
        resp = MagicMock()
        if "gemini-3.5-flash-lite:" in url:
            resp.status_code = 429
            resp.text = '{"error": {"code": 429, "message": "You exceeded your current quota..."}}'
            return resp
        elif "gemini-3.5-flash-lite-preview:" in url:
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{
                    "content": {"parts": [{"text": '{"matches": []}'}]}
                }]
            }
            return resp
        resp.status_code = 429
        return resp

    with patch("httpx.post", side_effect=mock_post):
        res = _call_gemini_generate_content(headers={}, payload={})
        assert res is not None
        assert res.status_code == 200
        assert any("gemini-3.5-flash-lite:" in u for u in called_urls)
        assert any("gemini-3.5-flash-lite-preview:" in u for u in called_urls)
        assert ai._ACTIVE_GEMINI_MODEL == "gemini-3.5-flash-lite-preview"
        assert get_gemini_model() == "gemini-3.5-flash-lite-preview"

    # Segunda chamada vai direto para o modelo em cache
    called_urls.clear()
    with patch("httpx.post", side_effect=mock_post):
        res = _call_gemini_generate_content(headers={}, payload={})
        assert res.status_code == 200
        assert not any("gemini-3.5-flash-lite:" in u for u in called_urls)
        assert any("gemini-3.5-flash-lite-preview:" in u for u in called_urls)


def test_gemini_read_timeout_auto_fallback(monkeypatch):
    """Testa auto-fallback quando o modelo Gemini lança httpx.ReadTimeout."""
    import httpx
    import app.ai as ai

    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    ai._ACTIVE_GEMINI_MODEL = None

    called_models = []

    def mock_post(url, **kwargs):
        model = url.split("/models/")[1].split(":")[0]
        called_models.append(model)
        if model == "gemini-3.5-flash-lite":
            raise httpx.ReadTimeout("The read operation timed out")
        elif model == "gemini-3.5-flash-lite-preview":
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{
                    "content": {"parts": [{"text": "OK"}]}
                }]
            }
            return resp
        resp = MagicMock()
        resp.status_code = 404
        return resp

    with patch("httpx.post", side_effect=mock_post):
        res = _call_gemini_generate_content(headers={}, payload={})
        assert res is not None
        assert res.status_code == 200
        assert called_models == ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite-preview"]
        assert ai._ACTIVE_GEMINI_MODEL == "gemini-3.5-flash-lite-preview"


def test_search_kabum_offers():
    """Testa busca estruturada na API v2 da KaBuM!."""
    mock_api_resp = MagicMock()
    mock_api_resp.status_code = 200
    mock_api_resp.json.return_value = {
        "data": [
            {
                "id": "123456",
                "attributes": {
                    "title": "Mouse Gamer KaBuM! RGB 12000 DPI",
                    "price_with_discount": 129.90,
                },
            },
            {
                "id": "789101",
                "attributes": {
                    "title": "Teclado Mecânico KaBuM!",
                    "price_with_discount": 259.90,
                },
            },
        ]
    }

    with patch("httpx.get", return_value=mock_api_resp):
        offers = search_kabum_offers("mouse gamer", limit=2)
        assert len(offers) == 2
        assert offers[0]["store"] == "KaBuM!"
        assert offers[0]["title"] == "Mouse Gamer KaBuM! RGB 12000 DPI"
        assert offers[0]["price"] == 129.90
        assert offers[0]["url"] == "https://www.kabum.com.br/produto/123456"
        assert offers[0]["source"] == "kabum:api"


def test_search_amazon_offers():
    """Testa busca estruturada e parsing da página de resultados da Amazon Brasil."""
    html_content = """
    <html>
      <body>
        <div data-component-type="s-search-result" data-asin="B08N5WRWNW">
          <h2><span>Mouse Gamer Logitech G203 RGB</span></h2>
          <span class="a-price"><span class="a-offscreen">R$ 139,90</span></span>
        </div>
      </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html_content

    with patch("httpx.get", return_value=mock_resp):
        offers = search_amazon_offers("mouse gamer", limit=2)
        assert len(offers) == 1
        assert offers[0]["store"] == "Amazon"
        assert offers[0]["title"] == "Mouse Gamer Logitech G203 RGB"
        assert offers[0]["price"] == 139.90
        assert offers[0]["url"] == "https://www.amazon.com.br/dp/B08N5WRWNW"
        assert offers[0]["source"] == "amazon:search"


def test_discover_product_links_multi_store_diversification(monkeypatch):
    """Testa descoberta unificada integrando Shopee, Mercado Livre, KaBuM! e Amazon com diversificação."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    shopee_items = [
        {"store": "Shopee", "title": "Mouse Shopee 1", "url": "https://shopee.com.br/m1", "price": 99.0, "source": "shopee:api"},
        {"store": "Shopee", "title": "Mouse Shopee 2", "url": "https://shopee.com.br/m2", "price": 109.0, "source": "shopee:api"},
    ]
    ml_items = [
        {"store": "Mercado Livre", "title": "Mouse ML 1", "url": "https://mercadolivre.com.br/p/MLB1", "price": 115.0, "source": "ml:api"},
        {"store": "Mercado Livre", "title": "Mouse ML 2", "url": "https://mercadolivre.com.br/p/MLB2", "price": 125.0, "source": "ml:api"},
    ]
    kabum_items = [
        {"store": "KaBuM!", "title": "Mouse KaBuM 1", "url": "https://kabum.com.br/produto/1", "price": 120.0, "source": "kabum:api"},
        {"store": "KaBuM!", "title": "Mouse KaBuM 2", "url": "https://kabum.com.br/produto/2", "price": 130.0, "source": "kabum:api"},
    ]
    amazon_items = [
        {"store": "Amazon", "title": "Mouse Amazon 1", "url": "https://amazon.com.br/dp/A1", "price": 140.0, "source": "amazon:search"},
        {"store": "Amazon", "title": "Mouse Amazon 2", "url": "https://amazon.com.br/dp/A2", "price": 150.0, "source": "amazon:search"},
    ]

    with patch("app.ai.search_shopee_offers", return_value=shopee_items), \
         patch("app.ai.search_mercadolivre_offers", return_value=ml_items), \
         patch("app.ai.search_kabum_offers", return_value=kabum_items), \
         patch("app.ai.search_amazon_offers", return_value=amazon_items):

        res = discover_product_links("Mouse Gamer", max_results=8)
        results = res["results"]
        assert len(results) == 8

        # Na 1ª rodada (primeiros 4 itens), deve haver exatamente 1 item de cada uma das 4 lojas
        first_four_stores = {r["store"] for r in results[:4]}
        assert first_four_stores == {"Shopee", "Mercado Livre", "KaBuM!", "Amazon"}

        # Na 2ª rodada, deve haver o 2º item de cada loja
        second_four_stores = {r["store"] for r in results[4:8]}
        assert second_four_stores == {"Shopee", "Mercado Livre", "KaBuM!", "Amazon"}


def test_is_brazilian_store_url():
    """Testa bloqueio de domínios/URLs estrangeiras e permissão de lojas brasileiras."""
    # URLs que devem ser barradas
    blocked_urls = [
        "https://www.mercadolibre.com.ar/p/MLA12345",
        "https://www.amazon.com/dp/B08N5WRWNW",
        "https://www.ebay.com/itm/12345678",
        "https://www.dell.com/en-us/shop/dell-laptops/spd/inspiron-15",
        "https://www.dell.com/en-gb/shop/laptops",
        "https://www.dell.com/es-es/shop",
        "https://www.walmart.com/ip/laptop/123",
        "https://www.bestbuy.com/site/laptop",
        "https://www.newegg.com/p/N82E16834156",
        "https://www.target.com/p/item/-/A-123",
        "https://www.bhphotovideo.com/c/product/123",
        "https://shopee.com.my/item-123",
        "https://mercadolivre.com/p/123",
        "",
        "not_a_valid_url",
    ]
    for url in blocked_urls:
        assert _is_brazilian_store_url(url) is False, f"Deveria ter bloqueado: {url}"

    # URLs que devem ser permitidas
    allowed_urls = [
        "https://www.mercadolivre.com.br/p/MLB12345",
        "https://www.amazon.com.br/dp/B08N5WRWNW",
        "https://www.kabum.com.br/produto/123456",
        "https://www.dell.com/pt-br/shop/dell-laptops/spd/inspiron-15",
        "https://www.magazineluiza.com.br/produto/123",
        "https://pt.aliexpress.com/item/1005001234.html",
        "https://shopee.com.br/product/123/456",
        "https://lenovo.com/br/pt/laptops/ideapad",
    ]
    for url in allowed_urls:
        assert _is_brazilian_store_url(url) is True, f"Deveria ter permitido: {url}"


def test_gemini_grounded_search_filters_foreign_urls_with_target_store(monkeypatch):
    """Testa que _gemini_grounded_search filtra URLs estrangeiras mesmo quando target_store é especificado."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://www.amazon.com/dp/B0CX_USA_STORE",
                                "title": "Notebook Acer Aspire 5 Amazon US",
                            }
                        },
                        {
                            "web": {
                                "uri": "https://www.amazon.com.br/dp/B0CX_BRAZIL_STORE",
                                "title": "Notebook Acer Aspire 5 Amazon BR",
                            }
                        },
                        {
                            "web": {
                                "uri": "https://www.ebay.com/itm/12345",
                                "title": "Notebook Acer Aspire 5 eBay",
                            }
                        },
                    ]
                },
                "content": {
                    "parts": [
                        {
                            "text": "Link estrangeiro no texto: https://amazon.co.uk/dp/B0CX_UK e link BR: https://www.amazon.com.br/dp/B0CX_ANOTHER_BR"
                        }
                    ]
                },
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        results = _gemini_grounded_search("Notebook Acer Aspire 5", "Notebook", target_store="Amazon")
        # Deve conter apenas as URLs brasileiras da Amazon
        assert len(results) == 2
        for r in results:
            assert "amazon.com.br" in r["url"]
            assert _is_brazilian_store_url(r["url"]) is True

        # Verifica que o prompt incluiu exigência de lojas brasileiras .com.br / Reais R$ mesmo com target_store
        call_kwargs = mock_post.call_args[1]
        sent_prompt = call_kwargs["json"]["contents"][0]["parts"][0]["text"]
        assert ".com.br" in sent_prompt
        assert "Reais R$" in sent_prompt


def test_validate_and_enrich_candidates_with_scraping():
    """Testa validação e enriquecimento: scraping de candidatos sem preço, descartando esgotados/estrangeiros."""
    candidates = [
        # 1. Candidato com preço válido já presente -> mantido sem scrape
        {"store": "Amazon", "title": "Produto 1", "url": "https://www.amazon.com.br/dp/1", "price": 1500.0, "source": "amazon:search"},
        # 2. Candidato estrangeiro -> descartado de imediato
        {"store": "Amazon", "title": "Produto US", "url": "https://www.amazon.com/dp/2", "price": 200.0, "source": "amazon:search"},
        # 3. Candidato sem preço (Grounding) ativo e com preço -> enriquecido e mantido
        {"store": "Dell", "title": "Dell Inspiron", "url": "https://www.dell.com/pt-br/shop/dell-laptops/spd/3", "price": None, "source": "gemini:grounded"},
        # 4. Candidato sem preço (Grounding) esgotado (available=False) -> descartado
        {"store": "KaBuM!", "title": "Placa de Vídeo Esgotada", "url": "https://www.kabum.com.br/produto/4", "price": None, "source": "gemini:grounded"},
        # 5. Candidato sem preço cujo scrape falha (retorna price=None) -> descartado
        {"store": "KaBuM!", "title": "Produto sem estoque", "url": "https://www.kabum.com.br/produto/5", "price": None, "source": "gemini:text"},
    ]

    def mock_scrape(url, css_selector=None):
        if "/spd/3" in url:
            return PriceResult(price=3200.0, available=True, final_price=2999.0, name="Dell Inspiron 15 3000 Atualizado")
        if "/produto/4" in url:
            return PriceResult(price=1800.0, available=False)
        if "/produto/5" in url:
            return PriceResult(price=None, available=False)
        return None

    with patch("app.ai.scrape", side_effect=mock_scrape):
        validated = _validate_and_enrich_candidates(candidates, max_scrape_checks=4)

        assert len(validated) == 2
        # Item 1 mantido
        assert validated[0]["url"] == "https://www.amazon.com.br/dp/1"
        assert validated[0]["price"] == 1500.0
        # Item 3 enriquecido
        assert validated[1]["url"] == "https://www.dell.com/pt-br/shop/dell-laptops/spd/3"
        assert validated[1]["price"] == 2999.0
        assert validated[1]["title"] == "Dell Inspiron 15 3000 Atualizado"


def test_discover_product_links_validates_grounding_candidates_and_filters_out_of_stock(monkeypatch):
    """Testa discover_product_links validando candidatos de Grounding via scrape e descartando esgotados."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    # APIs das lojas retornam vazio para forçar o Grounding
    with patch("app.ai.search_shopee_offers", return_value=[]), \
         patch("app.ai.search_mercadolivre_offers", return_value=[]), \
         patch("app.ai.search_kabum_offers", return_value=[]), \
         patch("app.ai.search_amazon_offers", return_value=[]):

        mock_grounded = [
            {"store": "Amazon", "title": "Notebook Acer Aspire", "url": "https://www.amazon.com.br/dp/B0_OK", "price": None, "source": "gemini:grounded"},
            {"store": "KaBuM!", "title": "Notebook Acer Esgotado", "url": "https://www.kabum.com.br/produto/B0_DEAD", "price": None, "source": "gemini:grounded"},
            {"store": "eBay", "title": "Notebook Acer eBay", "url": "https://www.ebay.com/itm/B0_EBAY", "price": None, "source": "gemini:grounded"},
        ]

        def mock_scrape(url, css_selector=None):
            if "B0_OK" in url:
                return PriceResult(price=2799.0, available=True, final_price=2799.0, name="Notebook Acer Aspire 5 A515")
            if "B0_DEAD" in url:
                return PriceResult(price=None, available=False)
            return None

        with patch("app.ai._gemini_grounded_search", return_value=mock_grounded), \
             patch("app.ai.scrape", side_effect=mock_scrape):

            disc = discover_product_links("Notebook Acer Aspire 5")
            results = disc["results"]

            # Somente o link ativo da Amazon BR deve sobreviver
            assert len(results) == 1
            assert results[0]["url"] == "https://www.amazon.com.br/dp/B0_OK"
            assert results[0]["price"] == 2799.0


def test_search_kabum_offers_ignores_unavailable_and_zero_price():
    """Testa que search_kabum_offers descarta itens indisponíveis ou sem preço válido (API v2 e fallback HTML)."""
    # 1. API v2
    api_resp = MagicMock()
    api_resp.status_code = 200
    api_resp.json.return_value = {
        "data": [
            {
                "id": "111",
                "attributes": {
                    "title": "Produto Esgotado",
                    "available": False,
                    "price_with_discount": 100.0,
                },
            },
            {
                "id": "222",
                "attributes": {
                    "title": "Produto Sem Preço",
                    "available": True,
                    "price_with_discount": None,
                },
            },
            {
                "id": "333",
                "attributes": {
                    "title": "Produto Preço Zero",
                    "available": True,
                    "price_with_discount": 0.0,
                },
            },
            {
                "id": "444",
                "attributes": {
                    "title": "Produto Válido e Em Estoque",
                    "available": True,
                    "price_with_discount": 299.90,
                },
            },
        ]
    }

    with patch("httpx.get", return_value=api_resp):
        offers = search_kabum_offers("produto teste", limit=5)
        assert len(offers) == 1
        assert offers[0]["title"] == "Produto Válido e Em Estoque"
        assert offers[0]["price"] == 299.90

    # 2. Fallback HTML quando a API v2 falha
    api_fail_resp = MagicMock()
    api_fail_resp.status_code = 500

    html_content = """
    <html>
      <body>
        <article class="productCard">
          <a href="/produto/555">Link</a>
          <span class="nameCard">Item Sem Preço Esgotado</span>
        </article>
        <article class="productCard">
          <a href="/produto/666">Link</a>
          <span class="nameCard">Item Com Preço Ativo</span>
          <span class="priceCard">R$ 159,90</span>
        </article>
      </body>
    </html>
    """
    html_resp = MagicMock()
    html_resp.status_code = 200
    html_resp.text = html_content

    def mock_get(url, **kwargs):
        if "catalog/v2" in url:
            return api_fail_resp
        return html_resp

    with patch("httpx.get", side_effect=mock_get):
        fallback_offers = search_kabum_offers("produto teste", limit=5)
        assert len(fallback_offers) == 1
        assert fallback_offers[0]["title"] == "Item Com Preço Ativo"
        assert fallback_offers[0]["price"] == 159.90


def test_search_amazon_offers_ignores_unavailable_without_price():
    """Testa que search_amazon_offers descarta produtos sem preço (esgotados/indisponíveis)."""
    html_content = """
    <html>
      <body>
        <!-- Item 1: Esgotado (sem bloco de preço) -->
        <div data-component-type="s-search-result" data-asin="B000000001">
          <h2><span>Mouse Gamer Esgotado Indisponível</span></h2>
        </div>
        <!-- Item 2: Ativo com preço -->
        <div data-component-type="s-search-result" data-asin="B000000002">
          <h2><span>Mouse Gamer Em Estoque</span></h2>
          <span class="a-price"><span class="a-offscreen">R$ 89,90</span></span>
        </div>
      </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html_content

    with patch("httpx.get", return_value=mock_resp):
        offers = search_amazon_offers("mouse gamer", limit=5)
        assert len(offers) == 1
        assert offers[0]["title"] == "Mouse Gamer Em Estoque"
        assert offers[0]["price"] == 89.90
        assert offers[0]["url"] == "https://www.amazon.com.br/dp/B000000002"

