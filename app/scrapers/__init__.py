"""Registro de scrapers.

Por padrão toda loja usa o scraper genérico (JSON-LD -> meta -> CSS).
Se uma loja específica precisar de tratamento próprio, crie uma função
`parse(html, css_selector) -> PriceResult` e registre o domínio abaixo.
"""
import os
import random
import time
from urllib.parse import urlparse

from . import amazon, mercadolivre
from .base import PriceResult, fetch_html, parse_html

CUSTOM_PARSERS: dict = {
    "www.amazon.com.br": amazon.parse,
    "amazon.com.br": amazon.parse,
    "www.mercadolivre.com.br": mercadolivre.parse,
    "mercadolivre.com.br": mercadolivre.parse,
    "produto.mercadolivre.com.br": mercadolivre.parse,
}

CUSTOM_HEADERS: dict = {
    "www.mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "produto.mercadolivre.com.br": mercadolivre.ML_HEADERS,
}


def scrape(url: str, css_selector: str | None = None,
           attempts: int = 3, retry_delay: tuple[float, float] = (3.0, 6.0)) -> PriceResult:
    """Baixa e parseia. Se houver API integrada (ex.: Mercado Livre), usa prioritariamente."""
    netloc = urlparse(url).netloc

    # 1. Suporte à API oficial do Mercado Livre
    if "mercadolivre.com.br" in netloc:
        ml_token = os.environ.get("MERCADOLIVRE_TOKEN", "").strip()
        if ml_token:
            api_res = mercadolivre.fetch_from_api(url, ml_token)
            if api_res and api_res.price is not None:
                return api_res

    # 2. Scraping HTTP tradicional
    parser = CUSTOM_PARSERS.get(netloc, parse_html)
    headers = CUSTOM_HEADERS.get(netloc)

    result = PriceResult(None, False, None, "not-found")
    for i in range(attempts):
        if i:
            time.sleep(random.uniform(*retry_delay))
        html = fetch_html(url, headers=headers)
        result = parser(html, css_selector)
        if result.price is not None:
            break
    return result


__all__ = ["scrape", "PriceResult", "CUSTOM_PARSERS", "CUSTOM_HEADERS"]
