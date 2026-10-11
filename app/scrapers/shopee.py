"""Parser específico para páginas e produtos da Shopee Brasil.

A Shopee é uma Single Page Application (SPA). Em requisições HTTP estáticas, ela serve
apenas um esqueleto React sem os dados do produto (requer execução de JavaScript ou
renderização via ScraperAPI com render=true).
"""
import json
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from .base import PriceResult, find_coupon, parse_brl, parse_html

SHOPEE_DOMAINS = {"shopee.com.br", "www.shopee.com.br", "s.shopee.com.br"}

SHOPEE_PRICE_SELECTORS = (
    ".G27UVZ",
    ".pmmxKx",
    ".IZPeQz",
    "div.pqTWkA",
    "div._3n5NQx",
    "div.product-price",
    "div[class*='product-price']",
    "div[class*='Price']",
)

OUT_OF_STOCK_INDICATORS = (
    "esgotado",
    "sold out",
    "produto esgotado",
    "produto indisponível",
    "fora de estoque",
    "não disponível",
)


def extract_shopee_ids(url: str) -> tuple[str | None, str | None]:
    """Extrai (shop_id, item_id) de uma URL da Shopee.
    
    Formatos suportados:
      - https://shopee.com.br/Nome-do-Produto-i.12345678.98765432
      - https://shopee.com.br/product/12345678/98765432
      - https://shopee.com.br/product/12345678/98765432?sp_atk=...
    """
    if not url:
        return None, None

    # Formato padrão canônico: -i.<shop_id>.<item_id>
    m = re.search(r"-i\.(\d+)\.(\d+)", url)
    if m:
        return m.group(1), m.group(2)

    # Formato alternativo de produto: /product/<shop_id>/<item_id>
    m = re.search(r"/product/(\d+)/(\d+)", url)
    if m:
        return m.group(1), m.group(2)

    return None, None


def _clean_product_name(raw_name: str | None) -> str | None:
    if not raw_name:
        return None
    name = raw_name.strip()
    for suffix in (" | Shopee Brasil", " - Shopee Brasil", " | Shopee", " - Shopee"):
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name or None


def _extract_price_from_text(text: str) -> float | None:
    """Extrai o menor preço de uma string que pode conter faixa (ex.: 'R$ 29,90 - R$ 59,90')."""
    if not text:
        return None
    # Busca todas as ocorrências de preços em Real
    matches = re.findall(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:\.\d{2})?)", text)
    if matches:
        prices = [p for p in (parse_brl(m) for m in matches) if p is not None]
        if prices:
            return min(prices)
    return parse_brl(text)


def _extract_from_scripts(soup: BeautifulSoup) -> tuple[float | None, str | None]:
    """Tenta localizar dados de preço em blocos de script/JSON da Shopee."""
    for script in soup.find_all("script"):
        text = script.string or ""
        if not text or len(text) < 20:
            continue

        # Procura por campos de preço típicos da Shopee (ex: price_min, price, price_before_discount)
        m = re.search(r'"price_min"\s*:\s*(\d+)', text) or re.search(r'"price"\s*:\s*(\d+)', text)
        if m:
            try:
                raw_val = int(m.group(1))
                # Na Shopee, os preços internos vêm multiplicados por 100.000 (ex.: 2990000 = R$ 29,90)
                if raw_val >= 10000:
                    calculated = round(raw_val / 100000.0, 2)
                    return calculated, "shopee:script-json"
                elif raw_val > 0:
                    return float(raw_val), "shopee:script-json"
            except (ValueError, TypeError):
                pass
    return None, None


def parse(html_str: str, css_selector: str | None = None, url: str | None = None) -> PriceResult:
    """Extrai preço e disponibilidade de uma página da Shopee."""
    soup = BeautifulSoup(html_str, "html.parser")

    # 1. Nome do produto
    name = None
    title_el = (
        soup.select_one("div._44qnta")
        or soup.select_one("span.product-briefing__title")
        or soup.select_one("h1.product-title")
        or soup.select_one("h1")
    )
    if title_el:
        name = _clean_product_name(title_el.get_text(strip=True))

    if not name:
        og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "title"})
        if og_title and og_title.get("content"):
            name = _clean_product_name(str(og_title["content"]))

    if not name and soup.title:
        name = _clean_product_name(soup.title.get_text(strip=True))

    # 2. Disponibilidade
    available = True
    page_text_lower = soup.get_text(" ", strip=True).lower()
    for oos in OUT_OF_STOCK_INDICATORS:
        if oos in page_text_lower:
            available = False
            break

    # 3. Seletor CSS customizado se fornecido
    if css_selector:
        el = soup.select_one(css_selector)
        if el:
            p = _extract_price_from_text(el.get_text(strip=True))
            if p is not None:
                code, final = find_coupon(soup, p)
                return PriceResult(
                    price=p,
                    available=available,
                    name=name,
                    source=f"css:{css_selector}",
                    coupon_code=code,
                    final_price=final or p,
                )

    # 4. JSON-LD estruturado (quando renderizado com dados schema.org)
    res_generic = parse_html(html_str)
    if res_generic.price is not None and res_generic.source == "json-ld":
        return PriceResult(
            price=res_generic.price,
            available=res_generic.available and available,
            name=name or res_generic.name,
            source="shopee:json-ld",
            coupon_code=res_generic.coupon_code,
            final_price=res_generic.final_price,
        )

    # 5. Seletores conhecidos da Shopee
    for sel in SHOPEE_PRICE_SELECTORS:
        for el in soup.select(sel):
            p = _extract_price_from_text(el.get_text(strip=True))
            if p is not None:
                code, final = find_coupon(soup, p)
                return PriceResult(
                    price=p,
                    available=available,
                    name=name,
                    source=f"shopee:{sel}",
                    coupon_code=code,
                    final_price=final or p,
                )

    # 6. Fallback para scripts internos ou estado da página
    script_price, script_src = _extract_from_scripts(soup)
    if script_price is not None:
        code, final = find_coupon(soup, script_price)
        return PriceResult(
            price=script_price,
            available=available,
            name=name,
            source=script_src or "shopee:script-json",
            coupon_code=code,
            final_price=final or script_price,
        )

    # 7. Busca em blocos genéricos de preço com R$
    for el in soup.find_all(["span", "div", "p"], string=re.compile(r"R\$\s*\d+")):
        p = _extract_price_from_text(el.get_text(strip=True))
        if p is not None:
            code, final = find_coupon(soup, p)
            return PriceResult(
                price=p,
                available=available,
                name=name,
                source="shopee:text-regex",
                coupon_code=code,
                final_price=final or p,
            )

    # Se não encontrou nada, a página veio como SPA pura sem JS executado
    return PriceResult(
        price=None,
        available=False,
        name=name,
        source="shopee:csr-js-required",
    )

