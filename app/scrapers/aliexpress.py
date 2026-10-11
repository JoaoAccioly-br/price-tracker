"""Parser específico para páginas e URLs do AliExpress."""
import re
import urllib.parse
from bs4 import BeautifulSoup

from .base import PriceResult, parse_brl


def extract_from_url(url: str) -> tuple[float | None, float | None]:
    """Extrai (preço_original, preço_promocional) do parâmetro pdp_npi presente em URLs do AliExpress."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    pdp_npi = qs.get("pdp_npi", [""])[0]
    if not pdp_npi:
        return None, None

    # Formato típico do pdp_npi: ...!BRL!229.01!99.93!...
    m = re.search(r"!([A-Z]{3})!([0-9.]+)!([0-9.]+)", pdp_npi)
    if m:
        try:
            _, orig, promo = m.groups()
            return float(orig), float(promo)
        except ValueError:
            pass
    return None, None


def parse(html_str: str, css_selector: str | None = None, url: str | None = None) -> PriceResult:
    soup = BeautifulSoup(html_str, "html.parser")

    # Nome do produto via og:title
    name = None
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        raw_title = str(og_title["content"])
        name = raw_title.replace(" - AliExpress", "").strip()

    # 1. Tenta extrair preço diretamente dos parâmetros da URL (pdp_npi)
    if url:
        orig, promo = extract_from_url(url)
        if promo is not None:
            return PriceResult(
                price=orig or promo,
                available=True,
                name=name,
                source="aliexpress:url-npi",
                final_price=promo,
            )

    # 2. Seletor CSS manual se fornecido
    if css_selector:
        el = soup.select_one(css_selector)
        if el:
            p = parse_brl(el.get_text())
            if p:
                return PriceResult(price=p, available=True, name=name, source=f"css:{css_selector}")

    # AliExpress usa Client-Side Rendering (CSR): o HTML inicial não possui preços se a URL for limpa
    return PriceResult(price=None, available=False, name=name, source="aliexpress:csr-js-required")

