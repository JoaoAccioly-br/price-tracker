from unittest.mock import MagicMock, patch
import pytest
import httpx
from app.scrapers import scrape
from app.scrapers.base import PriceResult, get_scraperapi_key, fetch_html_scraperapi


def test_get_scraperapi_key_from_env(monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_KEY", "test_key_123")
    assert get_scraperapi_key() == "test_key_123"


def test_get_scraperapi_key_none(monkeypatch):
    monkeypatch.delenv("SCRAPERAPI_KEY", raising=False)
    monkeypatch.delenv("SCRAPER_API_KEY", raising=False)
    assert get_scraperapi_key() is None


@patch("app.scrapers.fetch_html")
@patch("app.scrapers.fetch_html_scraperapi")
def test_scrape_direct_success_does_not_call_scraperapi(mock_api, mock_direct, monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_KEY", "dummy_key")
    mock_direct.return_value = """
    <html>
      <script type="application/ld+json">
        {"@type": "Product", "name": "Item", "offers": {"price": "199.90"}}
      </script>
    </html>
    """
    res = scrape("https://loja.com.br/produto/1")
    assert res.price == 199.90
    assert "scraperapi" not in res.source
    mock_direct.assert_called_once()
    mock_api.assert_not_called()


@patch("app.scrapers.fetch_html")
@patch("app.scrapers.fetch_html_scraperapi")
def test_scrape_403_triggers_scraperapi_fallback(mock_api, mock_direct, monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_KEY", "dummy_key")

    req = httpx.Request("GET", "https://www.kabum.com.br/produto/123")
    resp = httpx.Response(403, request=req)
    mock_direct.side_effect = httpx.HTTPStatusError("403 Forbidden", request=req, response=resp)

    mock_api.return_value = """
    <html>
      <script type="application/ld+json">
        {"@type": "Product", "name": "Mouse Gamer", "offers": {"price": "89.90"}}
      </script>
    </html>
    """

    res = scrape("https://www.kabum.com.br/produto/123")
    assert res.price == 89.90
    assert res.name == "Mouse Gamer"
    assert "scraperapi" in res.source
    mock_api.assert_called_once()


@patch("app.scrapers.fetch_html")
@patch("app.scrapers.fetch_html_scraperapi")
def test_scrape_amazon_captcha_triggers_scraperapi(mock_api, mock_direct, monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_KEY", "dummy_key")

    mock_direct.return_value = """
    <html>
      <form action="/errors/validateCaptcha">
        <h4>Digite os caracteres que você vê abaixo</h4>
      </form>
    </html>
    """

    mock_api.return_value = """
    <html>
      <h1 id="productTitle">Logitech G203</h1>
      <span class="priceToPay"><span class="a-offscreen">R$ 129,90</span></span>
    </html>
    """

    res = scrape("https://www.amazon.com.br/dp/B087CT8PWY")
    assert res.price == 129.90
    assert res.name == "Logitech G203"
    assert "scraperapi" in res.source
    mock_api.assert_called_once()


@patch("app.scrapers.fetch_html")
@patch("app.scrapers.fetch_html_scraperapi")
def test_scrape_shopee_csr_triggers_scraperapi_with_render_js(mock_api, mock_direct, monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_KEY", "dummy_key")

    mock_direct.return_value = "<html><div id='main'></div></html>"
    mock_api.return_value = """
    <html>
      <div class="_44qnta">Mouse Sem Fio Bluetooth</div>
      <div class="G27UVZ">R$ 49,90</div>
    </html>
    """

    res = scrape("https://shopee.com.br/Mouse-Sem-Fio-i.123.456")
    assert res.price == 49.90
    assert res.name == "Mouse Sem Fio Bluetooth"
    assert "scraperapi" in res.source
    # Verifica que render_js=True foi passado para o ScraperAPI
    mock_api.assert_called_once_with("https://shopee.com.br/Mouse-Sem-Fio-i.123.456", "dummy_key", render_js=True)


@patch("app.scrapers.fetch_html")
@patch("app.scrapers.fetch_html_scraperapi")
def test_scrape_shein_triggers_scraperapi_with_render_js(mock_api, mock_direct, monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_KEY", "dummy_key")

    req = httpx.Request("GET", "https://br.shein.com/item-p-123.html")
    resp = httpx.Response(403, request=req)
    mock_direct.side_effect = httpx.HTTPStatusError("403 Forbidden", request=req, response=resp)

    mock_api.return_value = """
    <html>
      <script type="application/ld+json">
        {"@type": "Product", "name": "Camiseta Básica", "offers": {"price": "39.90"}}
      </script>
    </html>
    """

    res = scrape("https://br.shein.com/item-p-123.html")
    assert res.price == 39.90
    assert res.name == "Camiseta Básica"
    assert "scraperapi" in res.source
    mock_api.assert_called_once_with("https://br.shein.com/item-p-123.html", "dummy_key", render_js=True)


