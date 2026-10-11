"""Parser da Amazon.

A Amazon não publica JSON-LD nem meta tags de preço, então o genérico falha.
O "preço a pagar" fica no bloco corePrice (apex), dentro de `.a-offscreen`.
"""
from bs4 import BeautifulSoup

from .base import PriceResult, parse_brl, parse_html

# em ordem de preferência: o preço principal ("a pagar") da página
PRICE_SELECTORS = (
    "#corePrice_feature_div .apex-pricetopay-value .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .apex-pricetopay-value .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-offscreen",
    "#corePrice_desktop .a-price .a-offscreen",
    "#corePrice_desktop .a-offscreen",
    "#apex_desktop .priceToPay .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    ".priceToPay .a-offscreen",
    ".reinventPricePriceToPayMargin.priceToPay .a-offscreen",
    "span.apexPriceToPay .a-offscreen",
    "#price_inside_buybox",
    "#newBuyBoxPrice",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#priceblock_saleprice",
    "span.a-price[data-a-size='xl'] .a-offscreen",
    "span.a-price[data-a-size='l'] .a-offscreen",
    "span.a-price .a-offscreen",
)
# inputs escondidos com o preço numérico (plano B)
VALUE_SELECTORS = (
    "#twister-plus-price-data-price",
    "input#attach-base-product-price",
    "input[name='displayedPrice']",
    "input#priceValue",
)

UNAVAILABLE = ("indisponível", "não disponível", "currently unavailable")


def _price_from_whole_fraction(el) -> float | None:
    """Às vezes `.a-offscreen` vem vazio e o preço está em whole + fraction."""
    whole = el.select_one(".a-price-whole")
    if not whole:
        return None
    frac = el.select_one(".a-price-fraction")
    text = whole.get_text(strip=True).rstrip(",") + "," + (frac.get_text(strip=True) if frac else "00")
    return parse_brl(text)


def _find_price(soup) -> tuple[float | None, str]:
    # 1. Seletores diretos com .a-offscreen
    for sel in PRICE_SELECTORS:
        for el in soup.select(sel):
            price = parse_brl(el.get_text(strip=True))
            if price is None and el.parent is not None:
                price = _price_from_whole_fraction(el.parent)
            if price is not None:
                return price, f"amazon:{sel}"

    # 2. Busca de whole + fraction em qualquer bloco de preço
    for el in soup.select(".a-price, .priceToPay, #corePriceDisplay_desktop_feature_div, #apex_desktop"):
        price = _price_from_whole_fraction(el)
        if price is not None:
            return price, "amazon:whole-fraction"

    # 3. Inputs escondidos
    for sel in VALUE_SELECTORS:
        el = soup.select_one(sel)
        if el and el.get("value"):
            price = parse_brl(el["value"])
            if price is not None:
                return price, f"amazon:{sel}"

    # 4. JSON em script a-state
    for script in soup.find_all("script", type="a-state"):
        try:
            import json
            s_data = json.loads(script.string or "")
            if isinstance(s_data, dict):
                p_val = s_data.get("priceAmount") or s_data.get("buyingPrice")
                if p_val is not None:
                    p = parse_brl(p_val)
                    if p is not None:
                        return p, "amazon:a-state"
        except Exception:
            pass

    return None, ""


def parse(html: str, css_selector: str | None = None) -> PriceResult:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.select_one("#productTitle")
    name = title.get_text(strip=True) if title else None

    # Detecta desafio de bot / CAPTCHA da Amazon
    if (
        "validateCaptcha" in html
        or "api-services-support@amazon.com" in html
        or "digite os caracteres que você vê abaixo" in html.lower()
        or "enter the characters you see below" in html.lower()
        or soup.select_one("form[action*='validateCaptcha']")
    ):
        return PriceResult(price=None, available=False, name=name, source="amazon:captcha")

    avail_el = soup.select_one("#availability")
    avail_text = avail_el.get_text(" ", strip=True).lower() if avail_el else ""
    available = not any(word in avail_text for word in UNAVAILABLE)

    # variante que a Amazon às vezes serve (sem preço no HTML: MAP escondido no apex_desktop).
    apex = soup.select_one("#apex_desktop")
    if apex and "adicione este item ao seu carrinho" in apex.get_text().lower():
        return PriceResult(None, False, name, "amazon:price-hidden")

    price, source = _find_price(soup)
    if price is not None:
        from .base import find_coupon
        code, final = find_coupon(soup, price)
        return PriceResult(price, available, name, source, coupon_code=code, final_price=final or price)

    # nada específico funcionou: tenta o genérico (inclui o seletor CSS do listing)
    return parse_html(html, css_selector)
