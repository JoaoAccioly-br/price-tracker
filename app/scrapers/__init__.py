"""Registro de scrapers.

Por padrão toda loja usa o scraper genérico (JSON-LD -> meta -> CSS).
Se uma loja específica precisar de tratamento próprio, crie uma função
`parse(html, css_selector) -> PriceResult` e registre o domínio abaixo.
"""
import os
import random
import time
from urllib.parse import urlparse

from . import aliexpress, amazon, mercadolivre
from .base import PriceResult, fetch_html, parse_html

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
    "www.mercadolivre.com.br": mercadolivre.parse,
    "mercadolivre.com.br": mercadolivre.parse,
    "produto.mercadolivre.com.br": mercadolivre.parse,
    "pt.aliexpress.com": aliexpress.parse,
    "www.aliexpress.com": aliexpress.parse,
    "aliexpress.com": aliexpress.parse,
    "m.aliexpress.com": aliexpress.parse,
}

CUSTOM_HEADERS: dict = {
    "www.mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "produto.mercadolivre.com.br": mercadolivre.ML_HEADERS,
    "www.kabum.com.br": KABUM_HEADERS,
    "kabum.com.br": KABUM_HEADERS,
}


def scrape(url: str, css_selector: str | None = None,
           attempts: int = 3, retry_delay: tuple[float, float] = (3.0, 6.0)) -> PriceResult:
    """Baixa e parseia. Se houver API integrada (ex.: Mercado Livre), usa prioritariamente."""
    netloc = urlparse(url).netloc

    # 1. Suporte à API oficial do Mercado Livre
    if "mercadolivre.com.br" in netloc:
        ml_token = mercadolivre.get_access_token()
        if ml_token:
            api_res = mercadolivre.fetch_from_api(url, ml_token)
            if api_res and api_res.price is not None:
                return api_res
            mercadolivre.log.info(
                "[ML] API oficial consultada mas não retornou preço válido para %s. Tentando scraping...", url
            )
        else:
            mercadolivre.log.info(
                "[ML] Nenhum token válido disponível para consulta. Usando scraping como fallback."
            )

    # 2. Scraping HTTP tradicional
    parser = CUSTOM_PARSERS.get(netloc, parse_html)
    headers = CUSTOM_HEADERS.get(netloc)

    result = PriceResult(None, False, None, "not-found")
    for i in range(attempts):
        if i:
            time.sleep(random.uniform(*retry_delay))
        html = fetch_html(url, headers=headers)
        if "aliexpress.com" in netloc:
            result = parser(html, css_selector, url=url)
        else:
            result = parser(html, css_selector)
        if result.price is not None:
            break
    return result


__all__ = ["scrape", "PriceResult", "CUSTOM_PARSERS", "CUSTOM_HEADERS"]
