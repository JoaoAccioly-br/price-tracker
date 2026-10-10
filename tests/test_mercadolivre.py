from unittest.mock import MagicMock, patch
from app.scrapers import mercadolivre


def test_extract_mlb_id():
    url_catalog = "https://www.mercadolivre.com.br/mouse-delux-m900-pro-paw3395-70h-bateria-8k-com-dock-preto/p/MLB38199035"
    assert mercadolivre.extract_mlb_id(url_catalog) == "MLB38199035"

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

