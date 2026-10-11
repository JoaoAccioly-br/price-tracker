import json
from unittest.mock import MagicMock, patch
import pytest

from app.scrapers import scrape
from app.scrapers.shopee import (
    extract_shopee_ids,
    fetch_from_api,
    generate_shopee_signature,
    get_shopee_credentials,
    scrape_shopee,
)


def test_extract_shopee_ids():
    url_canonical = "https://shopee.com.br/Mouse-Sem-Fio-Recarreg%C3%A1vel-i.382045718.8920197777"
    assert extract_shopee_ids(url_canonical) == ("382045718", "8920197777")

    url_product = "https://shopee.com.br/product/12345/67890?sp_atk=abcdef"
    assert extract_shopee_ids(url_product) == ("12345", "67890")

    assert extract_shopee_ids("https://www.google.com") == (None, None)
    assert extract_shopee_ids("") == (None, None)


def test_generate_shopee_signature():
    app_id = "123456"
    secret = "secret_key"
    payload = {"query": "{ test }"}

    with patch("time.time", return_value=1700000000):
        ts, sig = generate_shopee_signature(app_id, secret, payload)
        assert ts == "1700000000"
        assert len(sig) == 64  # SHA-256 hex digest
        assert isinstance(sig, str)


def test_get_shopee_credentials_from_env(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "my_app_123")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "my_secret_456")
    assert get_shopee_credentials() == ("my_app_123", "my_secret_456")


def test_get_shopee_credentials_missing(monkeypatch):
    monkeypatch.delenv("SHOPEE_APP_ID", raising=False)
    monkeypatch.delenv("SHOPEE_APP_SECRET", raising=False)
    assert get_shopee_credentials() == (None, None)


def test_scrape_shopee_no_credentials(monkeypatch):
    monkeypatch.delenv("SHOPEE_APP_ID", raising=False)
    monkeypatch.delenv("SHOPEE_APP_SECRET", raising=False)
    res = scrape_shopee("https://shopee.com.br/item-i.123.456")
    assert res.price is None
    assert res.available is False
    assert res.source == "shopee:no-credentials"


def test_fetch_from_api_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "productOfferV2": {
                "nodes": [
                    {
                        "itemId": 8920197777,
                        "productName": "Mouse Gamer Sem Fio RGB",
                        "productLink": "https://shopee.com.br/item",
                        "price": "49.90",
                        "priceMin": "49.90",
                        "priceMax": "59.90",
                    }
                ]
            }
        }
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        res = fetch_from_api(
            "https://shopee.com.br/Mouse-Gamer-i.382045718.8920197777",
            app_id="12345",
            secret="abcde",
        )
        assert res.price == 49.90
        assert res.name == "Mouse Gamer Sem Fio RGB"
        assert res.available is True
        assert res.source == "shopee:api"


def test_fetch_from_api_graphql_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "errors": [{"message": "Invalid Signature", "code": 10020}]
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        res = fetch_from_api(
            "https://shopee.com.br/Mouse-Gamer-i.382045718.8920197777",
            app_id="12345",
            secret="abcde",
        )
        assert res.price is None
        assert res.available is False
        assert res.source == "shopee:error-Invalid Signature"


def test_fetch_from_api_not_found():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {"productOfferV2": {"nodes": []}}
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        res = fetch_from_api(
            "https://shopee.com.br/Mouse-Gamer-i.382045718.8920197777",
            app_id="12345",
            secret="abcde",
        )
        assert res.price is None
        assert res.available is False
        assert res.source == "shopee:not-found"


def test_scrape_routing_shopee(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "app1")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "sec1")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "productOfferV2": {
                "nodes": [
                    {
                        "itemId": 456,
                        "productName": "Teclado",
                        "price": "99.00",
                    }
                ]
            }
        }
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        res = scrape("https://shopee.com.br/teclado-i.123.456")
        assert res.price == 99.00
        assert res.name == "Teclado"
        assert res.source == "shopee:api"
