"""Registro de scrapers.

Lojas com tratamento oficial/especializado:
- Mercado Livre: 100% via API Oficial (OAuth2)
- Shopee: 100% via API Oficial de Afiliados (GraphQL)
- AliExpress: Parâmetro promocional pdp_npi da URL
- Amazon Brasil: Seletores calibrados de Buybox

Demais lojas:
- Scraper genérico (JSON-LD -> meta -> CSS)
"""
import logging
import random
import time
from urllib.parse import urlparse

from . import aliexpress, amazon, mercadolivre, shopee
from .base import PriceResult, fetch_html, parse_html

log = logging.getLogger("collector")

AMAZON_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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

KABUM_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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

CUSTOM_PARSERS: dict = {
    "www.amazon.com.br": amazon.parse,
    "amazon.com.br": amazon.parse,
    "pt.aliexpress.com": aliexpress.parse,
    "www.aliexpress.com": aliexpress.parse,
    "aliexpress.com": aliexpress.parse,
    "m.aliexpress.com": aliexpress.parse,
}

CUSTOM_HEADERS: dict = {
    "www.amazon.com.br": AMAZON_HEADERS,
    "amazon.com.br": AMAZON_HEADERS,
    "www.kabum.com.br": KABUM_HEADERS,
    "kabum.com.br": KABUM_HEADERS,
}


def _matches_domain(netloc: str, domain: str) -> bool:
    """Verifica se o host da URL pertence exatamente ao domínio ou a um subdomínio legítimo."""
    host = netloc.split(":")[0].lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def scrape(url: str, css_selector: str | None = None,
           attempts: int = 3, retry_delay: tuple[float, float] = (3.0, 6.0)) -> PriceResult:
    """Baixa e parseia. Se houver API integrada (Mercado Livre e Shopee), usa prioritariamente."""
    netloc = (urlparse(url).hostname or urlparse(url).netloc).lower()

    # 1. Consulta exclusiva à API oficial do Mercado Livre (sem scraping HTTP)
    if _matches_domain(netloc, "mercadolivre.com.br"):
        return mercadolivre.scrape_ml(url)

    # 2. Consulta exclusiva à API oficial de Afiliados da Shopee (GraphQL)
    if _matches_domain(netloc, "shopee.com.br"):
        return shopee.scrape_shopee(url, css_selector)

    # 3. Scraping HTTP tradicional direto
    parser = CUSTOM_PARSERS.get(netloc, parse_html)
    headers = CUSTOM_HEADERS.get(netloc)

    result = PriceResult(None, False, None, "not-found")
    last_exc = None

    for i in range(attempts):
        if i:
            time.sleep(random.uniform(*retry_delay))
        try:
            html = fetch_html(url, headers=headers)
            if _matches_domain(netloc, "aliexpress.com"):
                result = parser(html, css_selector, url=url)
            else:
                result = parser(html, css_selector)
            if result.price is not None:
                return result
            # Se a Amazon retornou CAPTCHA ou AliExpress requereu JS, interrompe repetições diretas
            if result.source in ("amazon:captcha", "aliexpress:csr-js-required"):
                break
        except Exception as exc:
            last_exc = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (403, 503):
                break

    if last_exc:
        raise last_exc
    return result


__all__ = ["scrape", "PriceResult", "CUSTOM_PARSERS", "CUSTOM_HEADERS"]
