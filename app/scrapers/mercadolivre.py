"""Parser e cliente de API para o Mercado Livre.

Suporta:
1. API Oficial do Mercado Livre (se MERCADOLIVRE_TOKEN estiver configurado no .env):
   Consulta direta em https://api.mercadolibre.com/items/{MLB_ID}, sem risco de bloqueio WAF/CloudFront.
2. Web scraping como fallback.
"""
import os
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .base import PriceResult, parse_brl, parse_html

# Headers aceitos para scraping
ML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def extract_mlb_id(url: str) -> str | None:
    """Extrai o código do item MLB (ex.: MLB38199035 ou MLB-38199035) da URL."""
    # Procura padrões como /MLB12345678, /MLB-12345678 ou /p/MLB12345678
    m = re.search(r"(MLB-?\d{6,14})", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", "").upper()
    return None


def fetch_from_api(url: str, token: str) -> PriceResult | None:
    """Consulta a API oficial do Mercado Livre usando o token configurado."""
    mlb_id = extract_mlb_id(url)
    if not mlb_id:
        return None

    api_url = f"https://api.mercadolibre.com/items/{mlb_id}"
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/json",
    }
    try:
        r = httpx.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            price = float(data.get("price")) if data.get("price") is not None else None
            available = data.get("status") == "active" and (data.get("available_quantity", 0) > 0)
            name = data.get("title")
            return PriceResult(
                price=price,
                available=available,
                name=name,
                source="ml:api-oficial",
                final_price=price,
            )
    except Exception:
        pass
    return None


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
    if "suspicious-traffic" in html or "account-verification" in html:
        return PriceResult(price=None, available=False, name=name, source="ml:blocked")

    out_of_stock_el = soup.select_one(".ui-pdp-stock-information, .ui-pdp-buybox__quantity__status")
    if out_of_stock_el and any(w in out_of_stock_el.get_text().lower() for w in ["esgotado", "indisponível", "sem estoque"]):
        available = False

    # 3. Busca de preço:
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
