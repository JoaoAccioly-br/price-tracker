"""Registro de scrapers.

Por padrão toda loja usa o scraper genérico (JSON-LD -> meta -> CSS).
Se uma loja específica precisar de tratamento próprio, crie uma função
`parse(html, css_selector) -> PriceResult` e registre o domínio abaixo.
"""
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


def scrape(url: str, css_selector: str | None = None, use_browser: bool = False,
           attempts: int = 3, retry_delay: tuple[float, float] = (3.0, 6.0)) -> PriceResult:
    """Baixa e parseia. Se o preço não vier, tenta de novo."""
    netloc = urlparse(url).netloc
    parser = CUSTOM_PARSERS.get(netloc, parse_html)
    headers = CUSTOM_HEADERS.get(netloc)

    result = PriceResult(None, False, None, "not-found")
    for i in range(attempts):
        if i:
            time.sleep(random.uniform(*retry_delay))
        html = fetch_html(url, use_browser=use_browser, headers=headers)
        result = parser(html, css_selector)
        if result.price is not None:
            break
    return result


__all__ = ["scrape", "PriceResult", "CUSTOM_PARSERS", "CUSTOM_HEADERS"]
