"""Registro de scrapers.

Por padrão toda loja usa o scraper genérico (JSON-LD -> meta -> CSS).
Se uma loja específica precisar de tratamento próprio, crie uma função
`parse(html, css_selector) -> PriceResult` e registre o domínio abaixo.

Exemplo:
    from .minhaloja import parse as parse_minhaloja
    CUSTOM_PARSERS["www.minhaloja.com.br"] = parse_minhaloja
"""
import random
import time
from urllib.parse import urlparse

from . import amazon
from .base import PriceResult, fetch_html, parse_html

CUSTOM_PARSERS: dict = {
    "www.amazon.com.br": amazon.parse,
    "amazon.com.br": amazon.parse,
}


def scrape(url: str, css_selector: str | None = None, use_browser: bool = False,
           attempts: int = 3, retry_delay: tuple[float, float] = (3.0, 6.0)) -> PriceResult:
    """Baixa e parseia. Se o preço não vier, tenta de novo: algumas lojas
    (ex.: Amazon) alternam entre layouts com e sem preço a cada requisição."""
    parser = CUSTOM_PARSERS.get(urlparse(url).netloc, parse_html)
    result = PriceResult(None, False, None, "not-found")
    for i in range(attempts):
        if i:
            time.sleep(random.uniform(*retry_delay))
        result = parser(fetch_html(url, use_browser), css_selector)
        if result.price is not None:
            break
    return result


__all__ = ["scrape", "PriceResult", "CUSTOM_PARSERS"]
