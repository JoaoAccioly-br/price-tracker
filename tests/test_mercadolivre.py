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



