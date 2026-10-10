from app.scrapers import CUSTOM_PARSERS
from app.scrapers.amazon import parse

AMAZON_PAGE = """
<span id="productTitle"> Notebook Lenovo Ideapad Slim 3 </span>
<div id="corePrice_feature_div">
  <span class="a-price apex-pricetopay-value"><span class="a-offscreen">R$3.264,86</span></span>
</div>
<div id="tp_price_block_total_price_ww"><span class="a-price"><span class="a-offscreen">R$ 3.587,77</span></span></div>
<div id="availability"><span>Em estoque</span></div>
"""

AMAZON_WHOLE_FRACTION_UNAVAILABLE = """
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price priceToPay apex-pricetopay-value">
    <span class="a-offscreen"></span>
    <span aria-hidden="true"><span class="a-price-whole">4.199<span class="a-price-decimal">,</span></span><span class="a-price-fraction">90</span></span>
  </span>
</div>
<div id="availability"><span>Não disponível.</span></div>
"""

HIDDEN_INPUT_ONLY = '<input type="hidden" id="attach-base-product-price" value="3587.77">'


def test_amazon_registered():
    assert CUSTOM_PARSERS["www.amazon.com.br"] is parse


def test_amazon_price_to_pay():
    r = parse(AMAZON_PAGE)
    assert (r.price, r.available, r.name) == (3264.86, True, "Notebook Lenovo Ideapad Slim 3")


def test_amazon_whole_fraction_and_unavailable():
    r = parse(AMAZON_WHOLE_FRACTION_UNAVAILABLE)
    assert r.price == 4199.90 and r.available is False


def test_amazon_hidden_input_fallback():
    assert parse(HIDDEN_INPUT_ONLY).price == 3587.77


def test_amazon_not_found():
    assert parse("<html></html>").price is None


def test_amazon_price_hidden_variant():
    html = '<div id="apex_desktop">Para ver os detalhes do produto, adicione este item ao seu carrinho.</div>' \
           '<span class="a-price"><span class="a-offscreen">R$ 999,00</span></span>'  # preço de outro produto
    r = parse(html)
    assert r.price is None and r.source == "amazon:price-hidden"

