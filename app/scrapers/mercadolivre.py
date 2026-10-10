"""Parser e cliente de API para o Mercado Livre.

Suporta:
1. API Oficial do Mercado Livre (se MERCADOLIVRE_TOKEN estiver configurado no .env):
   Consulta direta em https://api.mercadolibre.com/items/{MLB_ID}, sem risco de bloqueio WAF/CloudFront.
2. Web scraping como fallback.
"""
import logging
import os
import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .base import PriceResult, parse_brl, parse_html

log = logging.getLogger("collector")

# Headers aceitos para scraping
ML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def get_access_token() -> str | None:
    """Obtém um access token válido para a API do Mercado Livre.

    Ordem de prioridade:
    1. Se MERCADOLIVRE_TOKEN (ou MERCADOLIVRE_ACCESS_TOKEN) estiver definido no ambiente, usa diretamente (modo manual).
    2. Se MERCADOLIVRE_CLIENT_ID e MERCADOLIVRE_CLIENT_SECRET estiverem definidos:
       - Verifica token salvo no banco (app_settings: ml_access_token e ml_token_expires_at).
       - Se ainda for válido (com margem de segurança de 5 minutos), retorna-o.
       - Se expirado ou ausente, renova automaticamente via refresh_token (do banco ou da env MERCADOLIVRE_REFRESH_TOKEN).
    """
    # 1. Modo manual via variável estática
    env_token = (
        os.environ.get("MERCADOLIVRE_TOKEN", "").strip()
        or os.environ.get("MERCADOLIVRE_ACCESS_TOKEN", "").strip()
    )
    if env_token:
        return env_token

    # 2. Modo automático OAuth com refresh token
    client_id = os.environ.get("MERCADOLIVRE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MERCADOLIVRE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        log.warning(
            "[ML-OAuth] Credenciais incompletas no Render: MERCADOLIVRE_CLIENT_ID=%s, MERCADOLIVRE_CLIENT_SECRET=%s",
            "CONFIGURADO" if client_id else "AUSENTE",
            "CONFIGURADO" if client_secret else "AUSENTE",
        )
        return None

    try:
        from ..db import get_setting

        token = get_setting("ml_access_token")
        expires_at_str = get_setting("ml_token_expires_at")
        if token and expires_at_str:
            try:
                expires_at = float(expires_at_str)
                if time.time() < (expires_at - 300):
                    return token
            except ValueError:
                pass
    except Exception as exc:
        log.warning("[ML-OAuth] Erro ao consultar token no banco: %s", exc)

    return refresh_access_token()


def refresh_access_token() -> str | None:
    """Renova o token OAuth junto à API do Mercado Livre e persiste o novo par no banco de dados."""
    client_id = os.environ.get("MERCADOLIVRE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MERCADOLIVRE_CLIENT_SECRET", "").strip()
    initial_refresh = os.environ.get("MERCADOLIVRE_REFRESH_TOKEN", "").strip()

    if not client_id or not client_secret:
        log.warning("[ML-OAuth] MERCADOLIVRE_CLIENT_ID e MERCADOLIVRE_CLIENT_SECRET não configurados.")
        return None

    from ..db import get_setting, set_setting

    refresh_token = get_setting("ml_refresh_token") or initial_refresh
    if not refresh_token:
        log.warning(
            "[ML-OAuth] Nenhum refresh token disponível (configure MERCADOLIVRE_REFRESH_TOKEN no ambiente)."
        )
        return None

    log.info("[ML-OAuth] Solicitando renovação do Access Token na API do Mercado Livre...")
    oauth_url = "https://api.mercadolibre.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        r = httpx.post(oauth_url, data=payload, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token")
            expires_in = data.get("expires_in", 21600)
            new_expires_at = time.time() + float(expires_in)

            if new_access_token:
                try:
                    set_setting("ml_access_token", new_access_token)
                    set_setting("ml_token_expires_at", str(new_expires_at))
                    if new_refresh_token:
                        set_setting("ml_refresh_token", new_refresh_token)
                except Exception as db_err:
                    log.warning("[ML-OAuth] Erro ao persistir tokens no banco: %s", db_err)

                log.info(
                    "[ML-OAuth] Token renovado com sucesso! Válido por %.1f horas.",
                    float(expires_in) / 3600,
                )
                return new_access_token
        else:
            log.warning(
                "[ML-OAuth] Falha ao renovar token (HTTP %s): %s",
                r.status_code,
                r.text[:300],
            )
    except Exception as exc:
        log.warning("[ML-OAuth] Exceção ao renovar token: %s", exc)
    return None



def extract_mlb_id(url: str) -> str | None:
    """Extrai o código do item MLB (ex.: MLB38199035 ou MLB-38199035) da URL."""
    # Procura padrões como /MLB12345678, /MLB-12345678 ou /p/MLB12345678
    m = re.search(r"(MLB-?\d{6,14})", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", "").upper()
    return None


def fetch_from_api(url: str, token: str) -> PriceResult | None:
    """Consulta a API oficial do Mercado Livre usando o token configurado."""
    mlb_id = extract_mlb_id(url)
    if not mlb_id:
        log.warning("[ML-API] Não foi possível extrair o código MLB da URL: %s", url)
        return None

    is_catalog = "/p/" in url
    endpoints = (
        [
            f"https://api.mercadolibre.com/products/{mlb_id}",
            f"https://api.mercadolibre.com/products/{mlb_id}/items",
            f"https://api.mercadolibre.com/sites/MLB/search?catalog_product_id={mlb_id}&sort=price_asc",
            f"https://api.mercadolibre.com/items/{mlb_id}",
        ]
        if is_catalog
        else [
            f"https://api.mercadolibre.com/items/{mlb_id}",
            f"https://api.mercadolibre.com/products/{mlb_id}",
            f"https://api.mercadolibre.com/sites/MLB/search?catalog_product_id={mlb_id}&sort=price_asc",
        ]
    )

    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/json",
    }
    for api_url in endpoints:
        try:
            r = httpx.get(api_url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                price = None
                available = True
                name = (
                    data.get("title") or data.get("name")
                    if isinstance(data, dict)
                    else None
                )

                # 1. Caso a resposta contenha uma lista de ofertas (ex.: /items ou busca por catalog_product_id)
                results_list = None
                if isinstance(data, list):
                    results_list = data
                elif isinstance(data, dict) and "results" in data and isinstance(data["results"], list):
                    results_list = data["results"]

                if results_list:
                    for item in results_list:
                        item_price = item.get("price")
                        if item_price is not None:
                            price = float(item_price)
                            available = item.get("status") == "active" or (item.get("available_quantity", 0) > 0)
                            name = item.get("title") or name
                            break

                # 2. Objeto de produto do catálogo
                if price is None and isinstance(data, dict):
                    bb = data.get("buy_box_winner")

                    # Se buy_box_winner tiver o preço diretamente
                    if isinstance(bb, dict):
                        if bb.get("price") is not None:
                            price = float(bb["price"])
                        if "status" in bb:
                            available = bb.get("status") == "active"

                        winner_item_id = bb.get("item_id") or bb.get("id")
                        if price is None and winner_item_id:
                            item_r = httpx.get(
                                f"https://api.mercadolibre.com/items/{winner_item_id}",
                                headers=headers,
                                timeout=10,
                            )
                            if item_r.status_code == 200:
                                item_data = item_r.json()
                                if item_data.get("price") is not None:
                                    price = float(item_data["price"])
                                    available = item_data.get("status") == "active" and (
                                        item_data.get("available_quantity", 0) > 0
                                    )
                                    name = item_data.get("title") or name

                    elif isinstance(bb, str) and bb.startswith("MLB"):
                        winner_item_id = bb
                        item_r = httpx.get(
                            f"https://api.mercadolibre.com/items/{winner_item_id}",
                            headers=headers,
                            timeout=10,
                        )
                        if item_r.status_code == 200:
                            item_data = item_r.json()
                            if item_data.get("price") is not None:
                                price = float(item_data["price"])
                                available = item_data.get("status") == "active" and (
                                    item_data.get("available_quantity", 0) > 0
                                )
                                name = item_data.get("title") or name

                    # 3. Anúncio direto (data['price'])
                    if price is None and data.get("price") is not None:
                        price = float(data["price"])
                        available = data.get("status", "active") == "active" and (data.get("available_quantity", 1) > 0)

                    # 4. Variações (pickers)
                    if price is None and "pickers" in data and isinstance(data["pickers"], list):
                        for picker in data["pickers"]:
                            for prod in picker.get("products", []):
                                prod_bb = prod.get("buy_box_winner")
                                if isinstance(prod_bb, dict) and prod_bb.get("price") is not None:
                                    price = float(prod_bb["price"])
                                    available = prod_bb.get("status", "active") == "active"
                                    break
                                elif isinstance(prod_bb, dict) and prod_bb.get("item_id"):
                                    v_id = prod_bb.get("item_id")
                                    v_r = httpx.get(f"https://api.mercadolibre.com/items/{v_id}", headers=headers, timeout=10)
                                    if v_r.status_code == 200 and v_r.json().get("price") is not None:
                                        price = float(v_r.json()["price"])
                                        available = True
                                        name = v_r.json().get("title") or name
                                        break
                            if price is not None:
                                break

                    # 5. Children IDs
                    if price is None and data.get("children_ids"):
                        for child_id in data["children_ids"][:3]:
                            child_r = httpx.get(
                                f"https://api.mercadolibre.com/products/{child_id}",
                                headers=headers,
                                timeout=10,
                            )
                            if child_r.status_code == 200:
                                child_data = child_r.json()
                                c_bb = child_data.get("buy_box_winner")
                                if isinstance(c_bb, dict) and c_bb.get("price") is not None:
                                    price = float(c_bb["price"])
                                    available = c_bb.get("status", "active") == "active"
                                    name = child_data.get("title") or name
                                    break
                                elif isinstance(c_bb, dict) and c_bb.get("item_id"):
                                    c_item_id = c_bb.get("item_id")
                                    c_ir = httpx.get(f"https://api.mercadolibre.com/items/{c_item_id}", headers=headers, timeout=10)
                                    if c_ir.status_code == 200 and c_ir.json().get("price") is not None:
                                        price = float(c_ir.json()["price"])
                                        available = True
                                        name = c_ir.json().get("title") or name
                                        break

                if price is not None:
                    log.info("[ML-API] Preço R$ %.2f obtido com sucesso via API oficial (%s)", price, api_url)
                    return PriceResult(
                        price=price,
                        available=available,
                        name=name,
                        source="ml:api-oficial",
                        final_price=price,
                    )
            elif r.status_code == 401:
                log.warning(
                    "[ML-API] HTTP 401 Unauthorized: token inválido ou expirado."
                )
                try:
                    from ..db import set_setting

                    set_setting("ml_token_expires_at", "0")
                except Exception:
                    pass
                break
            elif r.status_code == 403:
                log.warning("[ML-API] HTTP 403 Forbidden para %s.", api_url)
            elif r.status_code == 404:
                continue
            else:
                log.info("[ML-API] Status HTTP %s para %s", r.status_code, api_url)
        except Exception as exc:
            log.warning("[ML-API] Erro ao conectar na API (%s): %s", api_url, exc)
    return None


def parse(html: str, css_selector: str | None = None) -> PriceResult:
    """Extrai preço e disponibilidade do HTML de produtos do Mercado Livre."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Título do produto
    name = None
    title_el = soup.select_one("h1.ui-pdp-title") or soup.find("h1")
    if title_el:
        name = title_el.get_text(strip=True)

    # 2. Disponibilidade
    available = True
    if "suspicious-traffic" in html or "account-verification" in html:
        return PriceResult(price=None, available=False, name=name, source="ml:blocked")

    out_of_stock_el = soup.select_one(".ui-pdp-stock-information, .ui-pdp-buybox__quantity__status")
    if out_of_stock_el and any(w in out_of_stock_el.get_text().lower() for w in ["esgotado", "indisponível", "sem estoque"]):
        available = False

    # 3. Busca de preço:
    # A) Preço atual na buybox (.ui-pdp-price__second-line é o preço com desconto à vista)
    price = None
    source = ""

    pdp_price = soup.select_one(".ui-pdp-price__second-line") or soup.select_one(".ui-pdp-price")
    if pdp_price:
        frac = pdp_price.select_one(".andes-money-amount__fraction")
        cents = pdp_price.select_one(".andes-money-amount__cents")
        if frac:
            val_str = frac.get_text(strip=True)
            if cents:
                val_str += "," + cents.get_text(strip=True)
            price = parse_brl(val_str)
            if price is not None:
                source = "ml:ui-pdp-price"

    # B) Meta itemprop="price"
    if price is None:
        meta_price = soup.find("meta", itemprop="price")
        if meta_price and meta_price.get("content"):
            price = parse_brl(meta_price.get("content"))
            if price is not None:
                source = "ml:meta-price"

    # C) Seletor customizado ou genérico (JSON-LD)
    if price is None:
        gen = parse_html(html, css_selector)
        if gen.price is not None:
            return gen

    if price is None:
        return PriceResult(price=None, available=False, name=name, source="ml:not-found")

    from .base import find_coupon
    code, final = find_coupon(soup, price)
    return PriceResult(
        price=price,
        available=available,
        name=name,
        source=source,
        coupon_code=code,
        final_price=final or price,
    )
