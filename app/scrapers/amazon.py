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
    "#corePrice_feature_div .a-offscreen",
    "#apex_desktop .priceToPay .a-offscreen",
    "#corePrice_desktop .a-price .a-offscreen",
)
# inputs escondidos com o preço numérico (plano B)
VALUE_SELECTORS = ("#twister-plus-price-data-price", "input#attach-base-product-price")

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
    for sel in PRICE_SELECTORS:
        for el in soup.select(sel):
            price = parse_brl(el.get_text(strip=True))
            if price is None and el.parent is not None:
                price = _price_from_whole_fraction(el.parent)
            if price is not None:
                return price, f"amazon:{sel}"
    for sel in VALUE_SELECTORS:
        el = soup.select_one(sel)
        if el and el.get("value"):
            price = parse_brl(el["value"])
            if price is not None:
                return price, f"amazon:{sel}"
    return None, ""


def parse(html: str, css_selector: str | None = None) -> PriceResult:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.select_one("#productTitle")
    name = title.get_text(strip=True) if title else None

    avail_el = soup.select_one("#availability")
    avail_text = avail_el.get_text(" ", strip=True).lower() if avail_el else ""
    available = not any(word in avail_text for word in UNAVAILABLE)

    price, source = _find_price(soup)
    if price is not None:
        from .base import find_coupon
        code, final = find_coupon(soup, price)
        return PriceResult(price, available, name, source, coupon_code=code, final_price=final or price)

    # variante que a Amazon às vezes serve (sem preço no HTML). Vale tentar de novo.
    if "adicione este item ao seu carrinho" in html:
        return PriceResult(None, False, name, "amazon:price-hidden")

    # nada específico funcionou: tenta o genérico (inclui o seletor CSS do listing)
    return parse_html(html, css_selector)
