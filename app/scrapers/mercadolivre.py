"""Parser específico para o Mercado Livre.

O Mercado Livre possui proteções de tráfego que frequentemente bloqueiam
User-Agents genéricos, além de possuir elementos HTML próprios para
exibição de preços (andes-money-amount, itemprop="price", etc.).
"""
from bs4 import BeautifulSoup

from .base import PriceResult, parse_brl, parse_html

# Headers que simulam bot de indexação / navegador comum aceito pelo ML
ML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def parse(html: str, css_selector: str | None = None) -> PriceResult:
    """Extrai preço e disponibilidade do HTML de produtos do Mercado Livre."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Título do produto
    name = None
    title_el = soup.select_one("h1.ui-pdp-title") or soup.find("h1")
    if title_el:
        name = title_el.get_text(strip=True)

    # 2. Disponibilidade
    available = True
    if "suspicious-traffic" in html:
        return PriceResult(price=None, available=False, name=name, source="ml:blocked")

    out_of_stock_el = soup.select_one(".ui-pdp-stock-information, .ui-pdp-buybox__quantity__status")
    if out_of_stock_el and any(w in out_of_stock_el.get_text().lower() for w in ["esgotado", "indisponível", "sem estoque"]):
        available = False

    # 3. Busca de preço prioritária:
    # A) Preço atual na buybox (.ui-pdp-price__second-line é o preço com desconto à vista)
    price = None
    source = ""

    pdp_price = soup.select_one(".ui-pdp-price__second-line") or soup.select_one(".ui-pdp-price")
    if pdp_price:
        frac = pdp_price.select_one(".andes-money-amount__fraction")
        cents = pdp_price.select_one(".andes-money-amount__cents")
        if frac:
            val_str = frac.get_text(strip=True)
            if cents:
                val_str += "," + cents.get_text(strip=True)
            price = parse_brl(val_str)
            if price is not None:
                source = "ml:ui-pdp-price"

    # B) Meta itemprop="price"
    if price is None:
        meta_price = soup.find("meta", itemprop="price")
        if meta_price and meta_price.get("content"):
            price = parse_brl(meta_price.get("content"))
            if price is not None:
                source = "ml:meta-price"

    # C) Seletor customizado ou genérico (JSON-LD)
    if price is None:
        gen = parse_html(html, css_selector)
        if gen.price is not None:
            return gen

    if price is None:
        return PriceResult(price=None, available=False, name=name, source="ml:not-found")

    from .base import find_coupon
    code, final = find_coupon(soup, price)
    return PriceResult(
        price=price,
        available=available,
        name=name,
        source=source,
        coupon_code=code,
        final_price=final or price,
    )

