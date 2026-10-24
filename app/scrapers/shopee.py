"""Integração com a API Oficial de Afiliados da Shopee Brasil (Shopee Affiliate Open API).

Consulta itens via GraphQL (productOfferV2) com autenticação criptográfica SHA256.
Livre de bloqueios WAF / CAPTCHA e 100% leve (HTTP/JSON).
"""
import hashlib
import json
import logging
import os
import re
import time
from bs4 import BeautifulSoup
import httpx

from .base import PriceResult, find_coupon, parse_brl, parse_html

log = logging.getLogger("shopee")

SHOPEE_API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

SHOPEE_DOMAINS = {"shopee.com.br", "www.shopee.com.br", "s.shopee.com.br"}


def extract_shopee_ids(url: str) -> tuple[str | None, str | None]:
    """Extrai (shop_id, item_id) de uma URL da Shopee.

    Formatos suportados:
      - https://shopee.com.br/Nome-do-Produto-i.12345678.98765432
      - https://shopee.com.br/product/12345678/98765432
      - https://shopee.com.br/product/12345678/98765432?sp_atk=...
    """
    if not url:
        return None, None

    m = re.search(r"-i\.(\d+)\.(\d+)", url)
    if m:
        return m.group(1), m.group(2)

    m = re.search(r"/product/(\d+)/(\d+)", url)
    if m:
        return m.group(1), m.group(2)

    return None, None


def get_shopee_credentials() -> tuple[str | None, str | None]:
    """Obtém App ID e App Secret das variáveis de ambiente ou do banco."""
    app_id = os.environ.get("SHOPEE_APP_ID", "").strip() or None
    secret = os.environ.get("SHOPEE_APP_SECRET", "").strip() or None

    if not app_id or not secret:
        try:
            from ..db import get_setting

            db_app_id = get_setting("shopee_app_id")
            db_secret = get_setting("shopee_app_secret")
            if db_app_id and db_app_id.strip():
                app_id = db_app_id.strip()
            if db_secret and db_secret.strip():
                secret = db_secret.strip()
        except Exception:
            pass

    return app_id, secret


def generate_shopee_signature(app_id: str, secret: str, payload_dict: dict) -> tuple[str, str]:
    """Gera o timestamp em segundos e a assinatura SHA256 exigida pela Shopee Affiliate Open API.

    Fórmula oficial: Signature = SHA256(AppId + Timestamp + Payload + Secret)
    """
    timestamp = str(int(time.time()))
    payload_str = json.dumps(payload_dict, separators=(",", ":"))
    base_string = f"{app_id}{timestamp}{payload_str}{secret}"
    signature = hashlib.sha256(base_string.encode("utf-8")).hexdigest()
    return timestamp, signature


_SHOPEE_HTTP_CLIENT: httpx.Client | None = None


def _get_shopee_client(timeout: float = 15.0) -> httpx.Client:
    global _SHOPEE_HTTP_CLIENT
    if _SHOPEE_HTTP_CLIENT is None or _SHOPEE_HTTP_CLIENT.is_closed:
        _SHOPEE_HTTP_CLIENT = httpx.Client(timeout=timeout)
    return _SHOPEE_HTTP_CLIENT


def fetch_from_api(url: str, app_id: str, secret: str, timeout: float = 15.0) -> PriceResult:
    """Consulta os dados e o preço do item diretamente via API Oficial de Afiliados da Shopee."""
    shop_id, item_id = extract_shopee_ids(url)
    if not item_id or not item_id.isdigit():
        return PriceResult(price=None, available=False, name=None, source="shopee:invalid-url")

    # GraphQL query para buscar a oferta pelo itemId
    query_str = (
        f"query {{ productOfferV2(itemId: {int(item_id)}, limit: 1) {{ "
        f"nodes {{ itemId productName productLink priceMin priceMax price }} }} }}"
    )
    payload = {"query": query_str}

    timestamp, signature = generate_shopee_signature(app_id, secret, payload)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id},Timestamp={timestamp},Signature={signature}",
    }

    try:
        client = _get_shopee_client(timeout=timeout)
        resp = client.post(SHOPEE_API_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            log.warning("Shopee API retornou status HTTP %d: %s", resp.status_code, resp.text[:200])
            return PriceResult(price=None, available=False, name=None, source=f"shopee:http-{resp.status_code}")

        data = resp.json()

        # Trata erros do GraphQL
        errors = data.get("errors")
        if errors:
            err_msg = errors[0].get("message", "unknown error")
            log.warning("Shopee API retornou erro GraphQL: %s", err_msg)
            return PriceResult(price=None, available=False, name=None, source=f"shopee:error-{err_msg}")

        offer_data = data.get("data", {}).get("productOfferV2", {})
        nodes = offer_data.get("nodes", [])
        if not nodes:
            log.info("Shopee API: Nenhum resultado retornado para o itemId %s", item_id)
            return PriceResult(price=None, available=False, name=None, source="shopee:not-found")

        item_info = nodes[0]
        name = item_info.get("productName")
        raw_price = item_info.get("price") or item_info.get("priceMin") or item_info.get("priceMax")
        price = parse_brl(raw_price)

        if price is not None:
            log.info("Shopee API: Preço R$ %.2f obtido para '%s' (item %s)", price, name, item_id)
            return PriceResult(price=price, available=True, name=name, source="shopee:api", final_price=price)

        return PriceResult(price=None, available=False, name=name, source="shopee:price-missing")

    except Exception as exc:
        log.warning("Erro ao consultar a API da Shopee para %s: %s", url, exc)
        return PriceResult(price=None, available=False, name=None, source=f"shopee:exception-{type(exc).__name__}")


def search_shopee_offers(keyword: str, limit: int = 5) -> list[dict]:
    """Busca ofertas ativas na Shopee por palavra-chave usando a API Oficial de Afiliados."""
    app_id, secret = get_shopee_credentials()
    if not app_id or not secret or not keyword or not keyword.strip():
        return []

    safe_kw = keyword.replace("\\", "").replace('"', " ").strip()
    query_str = (
        f'query {{ productOfferV2(keyword: "{safe_kw}", sortType: 1, limit: {int(limit)}) {{ '
        f'nodes {{ itemId productName productLink priceMin priceMax price }} }} }}'
    )
    payload = {"query": query_str}
    timestamp, signature = generate_shopee_signature(app_id, secret, payload)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id},Timestamp={timestamp},Signature={signature}",
    }

    try:
        if getattr(httpx.post, "__module__", "") != "httpx":
            resp = httpx.post(SHOPEE_API_URL, json=payload, headers=headers, timeout=15.0)
        else:
            client = _get_shopee_client(timeout=15.0)
            resp = client.post(SHOPEE_API_URL, json=payload, headers=headers, timeout=15.0)

        if resp.status_code != 200:
            return []

        data = resp.json()
        if data.get("errors"):
            return []

        nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
        results = []
        for node in nodes:
            url = (node.get("productLink") or "").strip()
            title = (node.get("productName") or "").strip()
            if not url.startswith("http") or not title:
                continue
            raw_price = node.get("price") or node.get("priceMin") or node.get("priceMax")
            price = parse_brl(raw_price)
            results.append({
                "store": "Shopee",
                "title": title,
                "url": url,
                "price": price,
                "source": "shopee:api",
            })
        return results
    except Exception as exc:
        log.warning("Erro ao buscar ofertas na Shopee para '%s': %s", keyword, exc)
        return []


def scrape_shopee(url: str, css_selector: str | None = None) -> PriceResult:
    """Ponto de entrada para links da Shopee.

    1. Tenta prioritariamente via API Oficial com App ID e Secret.
    2. Se as chaves não estiverem configuradas, avisa claramente no log.
    """
    app_id, secret = get_shopee_credentials()
    if app_id and secret:
        return fetch_from_api(url, app_id, secret)

    log.warning(
        "[Shopee] Para consultar preços da Shopee sem bloqueios, configure SHOPEE_APP_ID e "
        "SHOPEE_APP_SECRET no seu .env (obtidos gratuitamente em https://affiliate.shopee.com.br/open_api)."
    )
    return PriceResult(price=None, available=False, name=None, source="shopee:no-credentials")
