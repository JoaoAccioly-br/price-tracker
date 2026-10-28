from unittest.mock import MagicMock, patch
from app.scrapers import mercadolivre


def test_extract_mlb_id():
    url_catalog = "https://www.mercadolivre.com.br/mouse-delux-m900-pro-paw3395-70h-bateria-8k-com-dock-preto/p/MLB38199035"
    assert mercadolivre.extract_mlb_id(url_catalog) == "MLB38199035"

    url_up = "https://www.mercadolivre.com.br/mouse-gamer-attack-shark-x11/up/MLBU3269725495"
    assert mercadolivre.extract_mlb_id(url_up) == "MLB3269725495"

    url_filters = "https://www.mercadolivre.com.br/mouse-gamer/up/MLBU3269725495?pdp_filters=item_id:MLB5474859082"
    assert mercadolivre.extract_mlb_id(url_filters) == "MLB3269725495"

    url_item = "https://produto.mercadolivre.com.br/MLB-123456789-mouse-gamer.html"
    assert mercadolivre.extract_mlb_id(url_item) == "MLB123456789"


@patch("httpx.get")
def test_fetch_from_api_catalog_product(mock_get):
    url = "https://www.mercadolivre.com.br/mouse-delux-m900-pro/p/MLB38199035"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "name": "Mouse Delux M900",
        "buy_box_winner": {
            "price": 179.90,
            "status": "active",
        },
    }
    mock_get.return_value = mock_resp

    res = mercadolivre.fetch_from_api(url, token="test_token")
    assert res is not None
    assert res.price == 179.90
    assert res.available is True
    assert res.source == "ml:api-oficial"


@patch("httpx.get")
def test_fetch_from_api_item(mock_get):
    url = "https://produto.mercadolivre.com.br/MLB-1234567890"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "title": "Item teste",
        "price": 99.50,
        "status": "active",
        "available_quantity": 5,
    }
    mock_get.return_value = mock_resp

    res = mercadolivre.fetch_from_api(url, token="test_token")
    assert res is not None
    assert res.price == 99.50
    assert res.available is True


@patch("httpx.post")
def test_refresh_access_token(mock_post, monkeypatch, tmp_path):
    from app import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_oauth.db")
    db.init_db()

    monkeypatch.setenv("MERCADOLIVRE_CLIENT_ID", "dummy_client")
    monkeypatch.setenv("MERCADOLIVRE_CLIENT_SECRET", "dummy_secret")
    monkeypatch.setenv("MERCADOLIVRE_REFRESH_TOKEN", "dummy_refresh")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "new_app_usr_token",
        "refresh_token": "new_refresh_token_rotated",
        "expires_in": 21600,
    }
    mock_post.return_value = mock_resp

    token = mercadolivre.refresh_access_token()
    assert token == "new_app_usr_token"
    assert db.get_setting("ml_access_token") == "new_app_usr_token"
    assert db.get_setting("ml_refresh_token") == "new_refresh_token_rotated"

    # Segunda chamada com token ainda válido não deve chamar httpx.post novamente
    mock_post.reset_mock()
    token_cached = mercadolivre.get_access_token()
    assert token_cached == "new_app_usr_token"
    mock_post.assert_not_called()


def test_extract_candidate_ids():
    url = "https://www.mercadolivre.com.br/mouse-gamer-attack-shark-x11/up/MLBU3269725495?pdp_filters=item_id:MLB5474859082"
    cids = mercadolivre.extract_candidate_ids(url)
    assert "MLB3269725495" in cids
    assert "MLBU3269725495" in cids
    assert "MLB5474859082" in cids


@patch("httpx.get")
def test_fetch_from_api_multiget(mock_get):
    url = "https://www.mercadolivre.com.br/item/MLB5474859082"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "code": 200,
            "body": {
                "id": "MLB5474859082",
                "title": "Mouse Gamer Attack Shark X11",
                "price": 129.90,
                "status": "active",
                "available_quantity": 8,
            },
        }
    ]
    mock_get.return_value = mock_resp

    res = mercadolivre.fetch_from_api(url, token="test_token")
    assert res.price == 129.90
    assert res.available is True
    assert res.source == "ml:api-oficial"


@patch("httpx.get")
def test_fetch_from_api_forbidden_returns_source(mock_get):
    url = "https://www.mercadolivre.com.br/item/MLB5474859082"
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"
    mock_get.return_value = mock_resp

    res = mercadolivre.fetch_from_api(url, token="test_token")
    assert res.price is None
    assert res.available is False
    assert res.source == "ml:api-403"


def test_scrape_ml_no_token(monkeypatch):
    monkeypatch.delenv("MERCADOLIVRE_TOKEN", raising=False)
    monkeypatch.delenv("MERCADOLIVRE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MERCADOLIVRE_CLIENT_ID", raising=False)
    monkeypatch.delenv("MERCADOLIVRE_CLIENT_SECRET", raising=False)

    res = mercadolivre.scrape_ml("https://www.mercadolivre.com.br/item/MLB123")
    assert res.price is None
    assert res.available is False
    assert res.source == "ml:sem-token"


@patch("app.scrapers.mercadolivre.scrape_ml")
@patch("app.scrapers.base.fetch_html")
def test_scrape_mercadolivre_never_calls_fetch_html(mock_fetch_html, mock_scrape_ml):
    from app.scrapers import scrape

    mock_scrape_ml.return_value = mercadolivre.PriceResult(price=100.0, available=True, source="ml:api-oficial")
    res = scrape("https://www.mercadolivre.com.br/item/MLB123")
    assert res.price == 100.0
    mock_scrape_ml.assert_called_once()
    mock_fetch_html.assert_not_called()


def test_fetch_from_api_catalog_product_buybox_null_fallback_search():
    """Testa catálogo /p/MLB... quando buy_box_winner é null e obtém preço via fallback /sites/MLB/search."""
    url = "https://www.mercadolivre.com.br/p/MLB61776379"

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        params = kwargs.get("params") or {}
        if "/products/MLB61776379" in req_url and "search" not in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "id": "MLB61776379",
                "name": "Processador AMD Ryzen 7 5700X",
                "buy_box_winner": None,
                "price": None,
            }
            return resp
        elif "/sites/MLB/search" in req_url and params.get("product_id") == "MLB61776379":
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {"id": "MLB123", "title": "Ryzen 7 5700X Oferta 1", "price": 1150.00},
                    {"id": "MLB456", "title": "Ryzen 7 5700X Oferta 2", "price": 1099.90},
                ]
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        res = mercadolivre.fetch_from_api(url, token="dummy_token")
        assert res.price == 1099.90
        assert res.available is True
        assert res.source == "ml:api-oficial"
        assert "Ryzen 7 5700X" in (res.name or "")


def test_fetch_from_api_catalog_product_buybox_null_fallback_html():
    """Testa catálogo /p/MLB... quando API não tem ofertas ativas mas HTML público tem preço."""
    url = "https://www.mercadolivre.com.br/p/MLB61776379"

    html_content = """
    <html>
      <head><title>Processador AMD Ryzen 7</title></head>
      <body>
        <h1 class="ui-pdp-title">Processador AMD Ryzen 7 5700X</h1>
        <div class="ui-pdp-price__second-line">
          <span class="andes-money-amount__fraction">1.250</span>
          <span class="andes-money-amount__cents">50</span>
        </div>
      </body>
    </html>
    """

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/MLB61776379" in req_url and "search" not in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "id": "MLB61776379",
                "name": "Processador AMD Ryzen 7 5700X",
                "buy_box_winner": None,
                "price": None,
            }
            return resp
        elif req_url == "https://www.mercadolivre.com.br/p/MLB61776379":
            resp.status_code = 200
            resp.text = html_content
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        res = mercadolivre.fetch_from_api(url, token="dummy_token")
        assert res.price == 1250.50
        assert res.available is True
        assert res.source == "ml:ui-pdp-price"
        assert "Processador AMD Ryzen 7 5700X" in (res.name or "")


def test_fetch_from_api_catalog_product_no_offers_returns_sem_oferta_ativa():
    """Testa que produto de catálogo 200 OK sem nenhuma oferta retorna ml:sem-oferta-ativa em vez de ml:api-404."""
    url = "https://www.mercadolivre.com.br/p/MLB61776379"

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/MLB61776379" in req_url and "search" not in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "id": "MLB61776379",
                "name": "Processador Esgotado",
                "buy_box_winner": None,
                "price": None,
            }
            return resp
        resp.status_code = 404
        resp.text = "Not found"
        return resp

    with patch("httpx.get", side_effect=mock_get):
        res = mercadolivre.fetch_from_api(url, token="dummy_token")
        assert res.price is None
        assert res.available is False
        assert res.source == "ml:sem-oferta-ativa"


def test_search_mercadolivre_offers_catalog_and_fallback():
    """Testa busca no ML descartando catálogo sem preço não enriquecido e retornando apenas anúncio ativo."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB61776379",
                        "name": "Catálogo Sem Preço",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB61776379",
                        "buy_box_winner": {"item_id": "MLB99887766", "price": None},
                    }
                ]
            }
            return resp
        elif "/sites/MLB/search" in req_url:
            if "product_id=" in req_url or (kwargs.get("params") or {}).get("product_id"):
                resp.status_code = 404
                return resp
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB554433",
                        "title": "Anúncio Geral Ativo",
                        "permalink": "https://produto.mercadolivre.com.br/MLB-554433",
                        "price": 899.00,
                    }
                ]
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("Ryzen 7", limit=3)
        # O catálogo sem preço não enriquecido NÃO é incluído no resultado final
        assert len(results) == 1
        assert results[0]["title"] == "Anúncio Geral Ativo"
        assert results[0]["price"] == 899.00


def test_search_mercadolivre_offers_enriches_via_winner_item_id():
    """Testa enriquecimento de catálogo unpriced via /items?ids={winner_item_id}."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB61776379",
                        "name": "Catálogo Sem Preço Direto",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB61776379",
                        "buy_box_winner": {"item_id": "MLB99887766", "price": None},
                    }
                ]
            }
            return resp
        elif "/sites/MLB/search" in req_url:
            if "product_id=" in req_url or (kwargs.get("params") or {}).get("product_id"):
                resp.status_code = 404
                return resp
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        elif "/products/MLB61776379" in req_url:
            resp.status_code = 200
            resp.json.return_value = {"id": "MLB61776379", "buy_box_winner": None}
            return resp
        elif "/items?ids=MLB99887766" in req_url:
            resp.status_code = 200
            resp.json.return_value = [
                {
                    "code": 200,
                    "body": {
                        "id": "MLB99887766",
                        "title": "Item Winner",
                        "price": 1050.00,
                        "status": "active",
                    },
                }
            ]
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("Ryzen 7", limit=3)
        assert len(results) == 1
        assert results[0]["title"] == "Catálogo Sem Preço Direto"
        assert results[0]["price"] == 1050.00
        assert results[0]["source"] == "ml:api-enriched"
        assert "pdp_filters=item_id:MLB99887766" in results[0]["url"]


def test_search_mercadolivre_offers_enriches_unpriced_catalog_items():
    """Testa enriquecimento de preço via /products/{prod_id} quando /products/search retorna buy_box_winner null."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB61776379",
                        "name": "Catálogo Unpriced",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB61776379",
                        "buy_box_winner": None,
                    }
                ]
            }
            return resp
        elif "/sites/MLB/search" in req_url:
            resp.status_code = 403
            resp.text = "Forbidden"
            return resp
        elif "/products/MLB61776379" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "id": "MLB61776379",
                "buy_box_winner": {
                    "price": 1399.90,
                    "status": "active",
                },
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("Ryzen", limit=3)
        assert len(results) == 1
        assert results[0]["title"] == "Catálogo Unpriced"
        assert results[0]["price"] == 1399.90
        assert results[0]["source"] == "ml:api-enriched"


def test_fetch_from_api_html_fallback_when_api_endpoints_404():
    """Testa fallback de HTML público quando todos os endpoints da API oficial retornam 404."""
    url = "https://www.mercadolivre.com.br/p/MLB38199035"
    html_page = """
    <html>
      <body>
        <h1 class="ui-pdp-title">Mouse Delux M900 Pro</h1>
        <div class="ui-pdp-price__second-line">
          <span class="andes-money-amount__fraction">189</span>
          <span class="andes-money-amount__cents">90</span>
        </div>
      </body>
    </html>
    """

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if req_url == url:
            resp.status_code = 200
            resp.text = html_page
            return resp
        # Todos os endpoints de API retornam 404
        resp.status_code = 404
        resp.text = "Not found"
        return resp

    with patch("httpx.get", side_effect=mock_get):
        res = mercadolivre.fetch_from_api(url, token="test_token")
        assert res.price == 189.90
        assert res.available is True
        assert res.source == "ml:ui-pdp-price"
        assert "Mouse Delux" in (res.name or "")


def test_search_mercadolivre_offers_public_html_fallback():
    """Testa busca no portal público lista.mercadolivre.com.br quando a API oficial não retorna resultados."""
    html_listing = """
    <html>
      <body>
        <div class="poly-card">
          <a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-998877-mouse-gamer.html">
            Mouse Gamer RGB 16000 DPI
          </a>
          <span class="andes-money-amount__fraction">249</span>
          <span class="andes-money-amount__cents">50</span>
        </div>
      </body>
    </html>
    """

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "lista.mercadolivre.com.br" in req_url:
            resp.status_code = 200
            resp.text = html_listing
            return resp
        # API retorna vazio
        if "/products/search" in req_url or "/sites/MLB/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("mouse gamer", limit=3)
        assert len(results) == 1
        assert results[0]["title"] == "Mouse Gamer RGB 16000 DPI"
        assert results[0]["price"] == 249.50
        assert results[0]["source"] == "ml:public-search"


def test_fetch_from_api_products_items_without_status_and_quantity():
    """Testa que /products/{id}/items sem 'status' e 'available_quantity' é lido com available=True."""
    url = "https://www.mercadolivre.com.br/p/MLB38199035"

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/MLB38199035/items" in req_url:
            resp.status_code = 200
            resp.json.return_value = [
                {
                    "item_id": "MLB11223344",
                    "title": "Mouse Gamer Delux",
                    "price": 199.90,
                }
            ]
            return resp
        elif "/products/MLB38199035" in req_url:
            resp.status_code = 200
            resp.json.return_value = {"id": "MLB38199035", "buy_box_winner": None}
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        res = mercadolivre.fetch_from_api(url, token="test_token")
        assert res.price == 199.90
        assert res.available is True
        assert res.source == "ml:api-oficial"


def test_fetch_from_api_buybox_winner_multiget_missing_available_quantity():
    """Testa buy_box_winner buscando /items?ids= com status 'active' e available_quantity ausente."""
    url = "https://www.mercadolivre.com.br/p/MLB38199035"

    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/MLB38199035" in req_url and "items" not in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "id": "MLB38199035",
                "name": "Mouse Gamer Delux M900",
                "buy_box_winner": {
                    "item_id": "MLB99887766",
                    "price": None,
                },
            }
            return resp
        elif "/items?ids=MLB99887766" in req_url:
            resp.status_code = 200
            resp.json.return_value = [
                {
                    "code": 200,
                    "body": {
                        "id": "MLB99887766",
                        "title": "Mouse Gamer Delux M900 Oferta",
                        "price": 185.00,
                        "status": "active",
                    },
                }
            ]
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        res = mercadolivre.fetch_from_api(url, token="test_token")
        assert res.price == 185.00
        assert res.available is True
        assert res.source == "ml:api-oficial"


def test_search_mercadolivre_offers_filters_international_and_unpriced():
    """Testa que itens CBT, estrangeiros, com moeda estrangeira e desprovidos de preço são filtrados."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    # 1. Domínio estrangeiro (ex.: Argentina)
                    {
                        "id": "MLA111",
                        "name": "Produto Argentino",
                        "permalink": "https://articulo.mercadolibre.com.ar/MLA-111",
                        "buy_box_winner": {"price": 100.0, "currency_id": "ARS"},
                    },
                    # 2. Moeda estrangeira USD
                    {
                        "id": "MLB222",
                        "name": "Produto Dólar",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB222",
                        "currency_id": "USD",
                        "buy_box_winner": {"price": 50.0},
                    },
                    # 3. Compra internacional via international_delivery_mode
                    {
                        "id": "MLB333",
                        "name": "Produto Importado CBT",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB333",
                        "international_delivery_mode": "customs",
                        "buy_box_winner": {"price": 200.0},
                    },
                    # 4. Compra internacional via tag cbt_item
                    {
                        "id": "MLB444",
                        "name": "Produto Tag CBT",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB444",
                        "tags": ["cbt_item", "other_tag"],
                        "buy_box_winner": {"price": 150.0},
                    },
                    # 5. Item de catálogo esgotado sem preço e sem enriquecimento
                    {
                        "id": "MLB555",
                        "name": "Catálogo Sem Preço",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB555",
                        "buy_box_winner": None,
                    },
                    # 6. Item BR válido e precificado
                    {
                        "id": "MLB666",
                        "name": "Produto Nacional Válido",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB666",
                        "currency_id": "BRL",
                        "buy_box_winner": {"price": 299.90, "currency_id": "BRL"},
                    },
                ]
            }
            return resp
        elif "/sites/MLB/search" in req_url:
            if "product_id=" in req_url or (kwargs.get("params") or {}).get("product_id"):
                resp.status_code = 404
                return resp
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    # Item CBT em busca geral
                    {
                        "title": "Anúncio Geral CBT",
                        "permalink": "https://produto.mercadolivre.com.br/MLB-777",
                        "price": 120.0,
                        "tags": ["cbt_item"],
                    },
                    # Item geral válido
                    {
                        "title": "Anúncio Geral Nacional",
                        "permalink": "https://produto.mercadolivre.com.br/MLB-888",
                        "price": 310.0,
                    },
                ]
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("teste", limit=5)
        titles = [r["title"] for r in results]
        assert "Produto Nacional Válido" in titles
        assert "Anúncio Geral Nacional" in titles
        assert "Produto Argentino" not in titles
        assert "Produto Dólar" not in titles
        assert "Produto Importado CBT" not in titles
        assert "Produto Tag CBT" not in titles
        assert "Catálogo Sem Preço" not in titles
        assert "Anúncio Geral CBT" not in titles
        assert all(r["price"] is not None for r in results)


def test_parse_fallback_json_ld_preserves_available():
    """Testa que o fallback parse_html preserva available e título do produto."""
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Teclado Mecânico JSON",
          "offers": {
            "@type": "Offer",
            "price": "250.00"
          }
        }
        </script>
      </head>
      <body>
        <h1 class="ui-pdp-title">Teclado Mecânico Gamer RGB</h1>
      </body>
    </html>
    """
    res = mercadolivre.parse(html)
    assert res.price == 250.00
    assert res.available is True
    assert res.name == "Teclado Mecânico Gamer RGB"


def test_search_mercadolivre_offers_enriches_via_catalog_items_endpoint():
    """Testa enriquecimento de catálogo unpriced através de /products/{p_id}/items quando buy_box_winner e /items?ids falham."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB990011",
                        "name": "Notebook Dell Inspiron 15",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB990011",
                        "buy_box_winner": None,
                    }
                ]
            }
            return resp
        elif "/sites/MLB/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        elif "/products/MLB990011/items" in req_url:
            resp.status_code = 200
            resp.json.return_value = [
                {
                    "item_id": "MLB556677",
                    "title": "Notebook Dell Inspiron 15 i5 8GB",
                    "price": 3299.00,
                    "status": "active",
                }
            ]
            return resp
        elif "/products/MLB990011" in req_url:
            resp.status_code = 200
            resp.json.return_value = {"id": "MLB990011", "buy_box_winner": None}
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("Notebook Dell", limit=3)
        assert len(results) == 1
        assert results[0]["title"] == "Notebook Dell Inspiron 15"
        assert results[0]["price"] == 3299.00
        assert results[0]["source"] == "ml:api-enriched"
        assert "pdp_filters=item_id:MLB556677" in results[0]["url"]


def test_search_mercadolivre_offers_enriches_via_sites_search_product_id():
    """Testa enriquecimento de catálogo unpriced através de /sites/MLB/search?product_id={p_id} quando /products/{p_id}/items falha."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        if "/products/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB887766",
                        "name": "Processador Ryzen 7",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB887766",
                        "buy_box_winner": None,
                    }
                ]
            }
            return resp
        elif "/products/MLB887766" in req_url:
            resp.status_code = 404
            return resp
        elif "product_id=MLB887766" in req_url:
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB112299",
                        "title": "Ryzen 7 5700X Oferta Barata",
                        "price": 1199.00,
                    }
                ]
            }
            return resp
        elif "/sites/MLB/search" in req_url:
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("Ryzen 7", limit=3)
        assert len(results) == 1
        assert results[0]["price"] == 1199.00
        assert results[0]["source"] == "ml:api-enriched"
        assert "pdp_filters=item_id:MLB112299" in results[0]["url"]


def test_search_mercadolivre_offers_retry_simplified_query():
    """Testa retry com query simplificada quando termos muito específicos (5+ palavras) retornam vazio na 1ª tentativa."""
    def mock_get(req_url, **kwargs):
        resp = MagicMock()
        params = kwargs.get("params") or {}
        # 1ª busca com a query longa completa -> vazio
        if "/products/search" in req_url and params.get("q") == "lenovo ideapad slim 3 ryzen 7735":
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        elif "/sites/MLB/search" in req_url and params.get("q") == "lenovo ideapad slim 3 ryzen 7735":
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp
        # 2ª busca com a query simplificada (primeiras 4 palavras)
        elif "/products/search" in req_url and params.get("q") == "lenovo ideapad slim 3":
            resp.status_code = 200
            resp.json.return_value = {
                "results": [
                    {
                        "id": "MLB778899",
                        "name": "Notebook Lenovo IdeaPad Slim 3",
                        "permalink": "https://www.mercadolivre.com.br/p/MLB778899",
                        "buy_box_winner": {
                            "price": 2899.00,
                            "status": "active",
                        },
                    }
                ]
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("httpx.get", side_effect=mock_get):
        results = mercadolivre.search_mercadolivre_offers("lenovo ideapad slim 3 ryzen 7735", limit=3)
        assert len(results) == 1
        assert results[0]["title"] == "Notebook Lenovo IdeaPad Slim 3"
        assert results[0]["price"] == 2899.00
        assert results[0]["source"] == "ml:api"





