import pytest
from app.scrapers.shopee import extract_shopee_ids, parse


def test_extract_shopee_ids():
    url_canonical = "https://shopee.com.br/Mouse-Sem-Fio-Recarreg%C3%A1vel-i.382045718.8920197777"
    assert extract_shopee_ids(url_canonical) == ("382045718", "8920197777")

    url_product = "https://shopee.com.br/product/12345/67890?sp_atk=abcdef"
    assert extract_shopee_ids(url_product) == ("12345", "67890")

    assert extract_shopee_ids("https://www.google.com") == (None, None)
    assert extract_shopee_ids("") == (None, None)


def test_parse_shopee_rendered_price():
    html = """
    <html>
      <head><title>Mouse Gamer RGB - Shopee Brasil</title></head>
      <body>
        <div class="_44qnta">Mouse Gamer RGB Ergonômico</div>
        <div class="G27UVZ">R$ 59,90</div>
      </body>
    </html>
    """
    res = parse(html)
    assert res.price == 59.90
    assert res.name == "Mouse Gamer RGB Ergonômico"
    assert res.available is True
    assert "shopee:.G27UVZ" in res.source


def test_parse_shopee_price_range():
    html = """
    <html>
      <body>
        <h1 class="product-title">Teclado Mecânico</h1>
        <div class="pmmxKx">R$ 149,90 - R$ 199,90</div>
      </body>
    </html>
    """
    res = parse(html)
    assert res.price == 149.90
    assert res.name == "Teclado Mecânico"
    assert res.available is True


def test_parse_shopee_script_json_micro_units():
    html = """
    <html>
      <head><title>Fone Bluetooth</title></head>
      <body>
        <script>
          window.__INITIAL_DATA__ = {"item": {"name": "Fone Bluetooth", "price_min": 2990000}};
        </script>
      </body>
    </html>
    """
    res = parse(html)
    assert res.price == 29.90
    assert res.name == "Fone Bluetooth"
    assert res.source == "shopee:script-json"


def test_parse_shopee_out_of_stock():
    html = """
    <html>
      <body>
        <h1>Cadeira Gamer</h1>
        <div class="G27UVZ">R$ 499,00</div>
        <span class="badge">Produto esgotado</span>
      </body>
    </html>
    """
    res = parse(html)
    assert res.price == 499.00
    assert res.available is False


def test_parse_shopee_coupon_detected():
    html = """
    <html>
      <body>
        <h1>Monitor 144Hz</h1>
        <div class="G27UVZ">R$ 1000,00</div>
        <div class="coupon-badge">Cupom: TECH10 (10% de desconto)</div>
      </body>
    </html>
    """
    res = parse(html)
    assert res.price == 1000.00
    assert res.coupon_code == "TECH10"
    assert res.final_price == 900.00


def test_parse_shopee_csr_skeleton_returns_not_found():
    html = """
    <!DOCTYPE html>
    <html>
      <head><meta charset="utf-8"></head>
      <body>
        <div id="main"></div>
        <script src="bundle.js"></script>
      </body>
    </html>
    """
    res = parse(html)
    assert res.price is None
    assert res.available is False
    assert res.source == "shopee:csr-js-required"

