"""Scraper genérico.

Ordem de tentativas para achar o preço numa página:
  1. JSON-LD (schema.org Product/Offer)  -> o mais estável
  2. <meta> de preço (og:price / product:price / itemprop="price")
  3. Seletor CSS cadastrado no listing   -> frágil, último recurso
"""
import json
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}


@dataclass
class PriceResult:
    price: float | None
    available: bool = True
    name: str | None = None
    source: str = ""  # de onde veio o preço (ajuda a depurar)
    coupon_code: str | None = None   # ex: "NOTE10", "150_OFF"
    final_price: float | None = None  # preço final com cupom aplicado (se houver)


# ---------------------------------------------------------------- utilidades
def parse_brl(value) -> float | None:
    """Converte 'R$ 3.499,90', '3499.90' ou 3499.9 em float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text:
        return None
    if "," in text and "." in text:
        # o último separador é o decimal
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{3}$", text):
        # '3.499' -> milhar (notebooks custam milhares, não 3 reais)
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _walk(node):
    """Percorre qualquer estrutura JSON devolvendo todos os dicts."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_product(d: dict) -> bool:
    t = d.get("@type")
    types = t if isinstance(t, list) else [t]
    return "Product" in types


def _offer_price(offers) -> tuple[float | None, bool]:
    """Extrai (preço, disponível) de um bloco 'offers'."""
    offers = offers if isinstance(offers, list) else [offers]
    best, available = None, False
    for off in offers:
        if not isinstance(off, dict):
            continue
        raw = off.get("price") or off.get("lowPrice")
        if raw is None and isinstance(off.get("priceSpecification"), dict):
            raw = off["priceSpecification"].get("price")
        price = parse_brl(raw)
        if price is None:
            continue
        in_stock = "OutOfStock" not in str(off.get("availability", ""))
        available = available or in_stock
        if best is None or price < best:
            best = price
    return best, available


COUPON_PATTERNS = (
    # Cupom de X% / X% OFF com cupom CODE
    r"(?i)cupom[:\s]+(?:de\s+)?([A-Z0-9_\-]{3,20})",
    r"(?i)use\s+o\s+cupom[:\s]+([A-Z0-9_\-]{3,20})",
    r"(?i)c[oó]digo[:\s]+([A-Z0-9_\-]{3,20})",
    r"(?i)coupon[:\s]+([A-Z0-9_\-]{3,20})",
)

DISCOUNT_PERCENT_PATTERN = re.compile(r"(\d{1,2})\s*%\s*(?:off|de\s+desconto)", re.IGNORECASE)
DISCOUNT_FIXED_PATTERN = re.compile(r"r\$\s*(\d+(?:[.,]\d{2})?)\s*(?:off|de\s+desconto)", re.IGNORECASE)


def find_coupon(soup: BeautifulSoup, base_price: float | None) -> tuple[str | None, float | None]:
    """Tenta identificar menções a cupom e calcular o preço final."""
    if not base_price:
        return None, None

    # Procura por elementos com classes / IDs sugestivos
    coupon_candidates = soup.select(
        "[class*='coupon'], [class*='Coupon'], [id*='coupon'], [id*='Coupon'], "
        "[class*='promo'], [class*='discount'], .voucher-badge"
    )

    for el in coupon_candidates:
        text = el.get_text(" ", strip=True)
        if len(text) > 200:  # evitar blocos muito grandes
            continue
        for pat in COUPON_PATTERNS:
            m = re.search(pat, text)
            if m:
                code = m.group(1).upper()
                # Verifica se há indicação de % ou R$ no mesmo elemento
                pm = DISCOUNT_PERCENT_PATTERN.search(text)
                if pm:
                    pct = float(pm.group(1)) / 100.0
                    return code, round(base_price * (1.0 - pct), 2)
                fm = DISCOUNT_FIXED_PATTERN.search(text)
                if fm:
                    fixed = parse_brl(fm.group(1))
                    if fixed and fixed < base_price:
                        return code, round(base_price - fixed, 2)
                return code, base_price

    return None, None


# --------------------------------------------------------------------- parser
def parse_html(html: str, css_selector: str | None = None) -> PriceResult:
    soup = BeautifulSoup(html, "html.parser")
    res: PriceResult | None = None

    # 1) JSON-LD
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for d in _walk(data):
            if _is_product(d) and d.get("offers"):
                price, available = _offer_price(d["offers"])
                if price is not None:
                    res = PriceResult(price, available, d.get("name"), "json-ld")
                    break
        if res:
            break

    # 2) meta tags
    if not res:
        for attrs in (
            {"property": "product:price:amount"},
            {"property": "og:price:amount"},
            {"itemprop": "price"},
        ):
            meta = soup.find(["meta", "span"], attrs=attrs)
            if meta:
                raw = meta.get("content") or meta.get_text()
                price = parse_brl(raw)
                if price is not None:
                    res = PriceResult(price, True, None, "meta")
                    break

    # 3) seletor CSS manual
    if not res and css_selector:
        el = soup.select_one(css_selector)
        if el:
            price = parse_brl(el.get_text())
            if price is not None:
                res = PriceResult(price, True, None, f"css:{css_selector}")

    if not res:
        return PriceResult(None, False, None, "not-found")

    # Detecta cupons se o preço foi encontrado
    code, final = find_coupon(soup, res.price)
    res.coupon_code = code
    res.final_price = final or res.price
    return res


# ---------------------------------------------------------------------- fetch
def fetch_html(url: str, use_browser: bool = False) -> str:
    if use_browser:
        return _fetch_with_browser(url)
    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _fetch_with_browser(url: str) -> str:
    # import tardio: Playwright é opcional (pip install playwright && playwright install chromium)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)  # deixa o JS da página rodar
        html = page.content()
        browser.close()
    return html


def scrape(url: str, css_selector: str | None = None, use_browser: bool = False) -> PriceResult:
    return parse_html(fetch_html(url, use_browser), css_selector)
