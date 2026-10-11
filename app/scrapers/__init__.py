"""Registro de scrapers.

Por padrão toda loja usa o scraper genérico (JSON-LD -> meta -> CSS).
Se uma loja específica precisar de tratamento próprio, crie uma função
`parse(html, css_selector) -> PriceResult` e registre o domínio abaixo.
"""
import logging
import os
import random
import time
from urllib.parse import urlparse

from . import aliexpress, amazon, mercadolivre, shopee
from .base import PriceResult, fetch_html, fetch_html_scraperapi, get_scraperapi_key, parse_html

log = logging.getLogger("collector")

# Domínios que são SPAs ou requerem renderização de JavaScript / resolução de anti-bot
JS_RENDER_DOMAINS: set[str] = {
    "shopee.com.br",
    "shein.com",
    "tiktok.com",
}

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

SHOPEE_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://shopee.com.br/",
}

CUSTOM_PARSERS: dict = {
    "www.amazon.com.br": amazon.parse,
    "amazon.com.br": amazon.parse,
    "www.mercadolivre.com.br": mercadolivre.parse,
    "mercadolivre.com.br": mercadolivre.parse,
    "produto.mercadolivre.com.br": mercadolivre.parse,
    "pt.aliexpress.com": aliexpress.parse,
    "www.aliexpress.com": aliexpress.parse,
    "aliexpress.com": aliexpress.parse,
    "m.aliexpress.com": aliexpress.parse,
    "shopee.com.br": shopee.parse,
    "www.shopee.com.br": shopee.parse,
    "s.shopee.com.br": shopee.parse,
}

CUSTOM_HEADERS: dict = {
    "www.amazon.com.br": AMAZON_HEADERS,
    "amazon.com.br": AMAZON_HEADERS,
    "www.mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "produto.mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "www.kabum.com.br": KABUM_HEADERS,
    "kabum.com.br": KABUM_HEADERS,
    "shopee.com.br": SHOPEE_HEADERS,
    "www.shopee.com.br": SHOPEE_HEADERS,
    "s.shopee.com.br": SHOPEE_HEADERS,
}


def scrape(url: str, css_selector: str | None = None,
           attempts: int = 3, retry_delay: tuple[float, float] = (3.0, 6.0)) -> PriceResult:
    """Baixa e parseia. Se houver API integrada (ex.: Mercado Livre), usa prioritariamente.
    Caso o scraping direto falhe ou seja bloqueado (403/CAPTCHA/CSR), tenta fallback via ScraperAPI (se configurado).
    """
    netloc = urlparse(url).netloc

    # 1. Consulta exclusiva à API oficial do Mercado Livre (sem scraping HTTP)
    if "mercadolivre.com.br" in netloc:
        return mercadolivre.scrape_ml(url)

    # 2. Scraping HTTP tradicional direto
    parser = CUSTOM_PARSERS.get(netloc, parse_html)
    headers = CUSTOM_HEADERS.get(netloc)

    result = PriceResult(None, False, None, "not-found")
    last_exc = None
    direct_success = False

    for i in range(attempts):
        if i:
            time.sleep(random.uniform(*retry_delay))
        try:
            html = fetch_html(url, headers=headers)
            if "aliexpress.com" in netloc or "shopee.com.br" in netloc:
                result = parser(html, css_selector, url=url)
            else:
                result = parser(html, css_selector)
            if result.price is not None:
                direct_success = True
                break
            # Se a Amazon retornou CAPTCHA ou a loja requer JS de SPA, interrompe repetições diretas
            if result.source in ("amazon:captcha", "shopee:csr-js-required", "aliexpress:csr-js-required"):
                break
        except Exception as exc:
            last_exc = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (403, 503):
                break

    if direct_success and result.price is not None:
        return result

    # 3. Fallback inteligente via ScraperAPI (apenas se configurado e o direto falhou)
    scraperapi_key = get_scraperapi_key()
    if scraperapi_key:
        fail_reason = str(last_exc) if last_exc else result.source
        log.info("[ScraperAPI] Ativando fallback para %s (motivo: %s)", url, fail_reason)
        try:
            render_js = (
                any(d in netloc for d in JS_RENDER_DOMAINS)
                or os.environ.get("SCRAPERAPI_RENDER_JS", "").strip().lower() in ("true", "1")
            )
            html = fetch_html_scraperapi(url, scraperapi_key, render_js=render_js)
            if "aliexpress.com" in netloc or "shopee.com.br" in netloc:
                res_api = parser(html, css_selector, url=url)
            else:
                res_api = parser(html, css_selector)

            if res_api.price is not None:
                res_api.source = f"{res_api.source}+scraperapi"
                log.info("[ScraperAPI] Sucesso! Preço R$ %.2f obtido para %s", res_api.price, url)
                return res_api
            else:
                log.warning(
                    "[ScraperAPI] Consulta executada mas nenhum preço encontrado (%s) em %s",
                    res_api.source, url
                )
                return res_api
        except Exception as api_err:
            log.warning("[ScraperAPI] Erro ao consultar ScraperAPI para %s: %s", url, api_err)

    if last_exc:
        raise last_exc
    return result


__all__ = ["scrape", "PriceResult", "CUSTOM_PARSERS", "CUSTOM_HEADERS", "JS_RENDER_DOMAINS"]
