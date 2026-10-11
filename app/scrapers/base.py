"""Scraper genérico.

Ordem de tentativas para achar o preço numa página:
  1. JSON-LD (schema.org Product/Offer)  -> o mais estável
  2. <meta> de preço (og:price / product:price / itemprop="price")
  3. Seletor CSS cadastrado no listing   -> frágil, último recurso
"""
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
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
MAX_HTML_BYTES = 8 * 1024 * 1024  # 8 MB: páginas de produto raramente passam de 2 MB


class UnsafeURLError(ValueError):
    """URL aponta para rede interna / esquema não permitido (proteção contra SSRF)."""


def assert_public_url(url: str) -> None:
    """Garante que a URL é http(s) e que o host resolve só para IPs públicos.

    Sem isso, qualquer pessoa com acesso à API poderia cadastrar
    http://127.0.0.1:xxxx, http://169.254.169.254 (metadados da cloud) ou IPs
    da rede interna e usar o coletor para sondar serviços privados (SSRF).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURLError(f"URL não permitida: {url!r}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Host não resolvido: {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise UnsafeURLError(f"Host {parsed.hostname} aponta para IP não público ({ip})")


def _check_request(request: httpx.Request) -> None:
    # chamado pelo httpx a cada requisição, inclusive nos redirects
    assert_public_url(str(request.url))


def fetch_html(url: str, headers: dict | None = None) -> str:
    assert_public_url(url)
    req_headers = {**HEADERS, **(headers or {})}
    with httpx.Client(
        headers=req_headers, timeout=20, follow_redirects=True, max_redirects=5,
        event_hooks={"request": [_check_request]},
    ) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise ValueError(f"Resposta maior que {MAX_HTML_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")


def get_scraperapi_key() -> str | None:
    """Obtém a chave da ScraperAPI das variáveis de ambiente ou do banco de dados."""
    key = os.environ.get("SCRAPERAPI_KEY", "").strip() or os.environ.get("SCRAPER_API_KEY", "").strip()
    if key:
        return key
    try:
        from ..db import get_setting

        db_key = get_setting("scraperapi_key")
        if db_key and db_key.strip():
            return db_key.strip()
    except Exception:
        pass
    return None


def fetch_html_scraperapi(url: str, api_key: str, render_js: bool = False, timeout: float = 45.0) -> str:
    """Busca o HTML via ScraperAPI como plano de contingência.
    
    Quando render_js=True, o ScraperAPI renderiza JavaScript (necessário para
    SPAs como Shopee, SHEIN e TikTok Shop).
    """
    assert_public_url(url)
    params = {
        "api_key": api_key.strip(),
        "url": url,
    }
    if render_js:
        params["render"] = "true"
    api_endpoint = "https://api.scraperapi.com/"
    req_timeout = timeout if render_js else 20.0
    with httpx.Client(timeout=req_timeout, follow_redirects=True) as client:
        r = client.get(api_endpoint, params=params)
        r.raise_for_status()
        return r.text


def scrape(url: str, css_selector: str | None = None) -> PriceResult:
    return parse_html(fetch_html(url), css_selector)
