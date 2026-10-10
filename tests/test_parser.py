import pytest

from app.scrapers.base import parse_brl, parse_html

JSON_LD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Notebook Teste 15",
 "offers":{"@type":"Offer","price":"3499.90","priceCurrency":"BRL",
           "availability":"https://schema.org/InStock"}}
</script></head><body></body></html>
"""

JSON_LD_GRAPH_OUT_OF_STOCK = """
<script type="application/ld+json">
{"@graph":[{"@type":"WebSite"},
  {"@type":["Product"],"name":"X","offers":[
     {"price":4200,"availability":"https://schema.org/OutOfStock"}]}]}
</script>
"""

META_PAGE = '<meta property="product:price:amount" content="2.899,00">'
CSS_PAGE = '<div><span class="preco">R$ 5.199,99</span></div>'


@pytest.mark.parametrize("raw,expected", [
    ("R$ 3.499,90", 3499.90),
    ("3499.90", 3499.90),
    ("3.499", 3499.0),
    ("1.299,00", 1299.0),
    (3499.9, 3499.9),
    ("sem preço", None),
    (None, None),
])
def test_parse_brl(raw, expected):
    assert parse_brl(raw) == expected


def test_json_ld():
    r = parse_html(JSON_LD_PAGE)
    assert (r.price, r.available, r.name, r.source) == (3499.90, True, "Notebook Teste 15", "json-ld")


def test_json_ld_graph_and_out_of_stock():
    r = parse_html(JSON_LD_GRAPH_OUT_OF_STOCK)
    assert r.price == 4200.0 and r.available is False


def test_meta_fallback():
    r = parse_html(META_PAGE)
    assert r.price == 2899.0 and r.source == "meta"


def test_css_fallback():
    r = parse_html(CSS_PAGE, css_selector="span.preco")
    assert r.price == 5199.99


def test_not_found():
    assert parse_html("<html></html>").price is None


COUPON_PERCENT_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Notebook Cupom",
 "offers":{"@type":"Offer","price":"4000.00","priceCurrency":"BRL","availability":"https://schema.org/InStock"}}
</script></head><body>
<div class="coupon-badge">Cupom: NOTE10 (10% de desconto)</div>
</body></html>
"""

COUPON_FIXED_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Notebook Fixo",
 "offers":{"@type":"Offer","price":"3000.00","priceCurrency":"BRL","availability":"https://schema.org/InStock"}}
</script></head><body>
<div class="promo-box">Use o cupom: DESCONTO200 (R$ 200,00 off)</div>
</body></html>
"""


def test_coupon_percent_detection():
    r = parse_html(COUPON_PERCENT_PAGE)
    assert r.price == 4000.0
    assert r.coupon_code == "NOTE10"
    assert r.final_price == 3600.0


def test_coupon_fixed_detection():
    r = parse_html(COUPON_FIXED_PAGE)
    assert r.price == 3000.0
    assert r.coupon_code == "DESCONTO200"
    assert r.final_price == 2800.0
