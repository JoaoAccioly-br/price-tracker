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

_ML_HTTP_CLIENT: httpx.Client | None = None


def _get_ml_client() -> httpx.Client:
    global _ML_HTTP_CLIENT
    if _ML_HTTP_CLIENT is None or _ML_HTTP_CLIENT.is_closed:
        _ML_HTTP_CLIENT = httpx.Client(timeout=10)
    return _ML_HTTP_CLIENT


def _http_get(url: str, **kwargs):
    if getattr(httpx.get, "__module__", "") != "httpx":
        return httpx.get(url, **kwargs)
    return _get_ml_client().get(url, **kwargs)


def _http_post(url: str, **kwargs):
    if getattr(httpx.post, "__module__", "") != "httpx":
        return httpx.post(url, **kwargs)
    return _get_ml_client().post(url, **kwargs)


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

    from ..db import get_setting, set_settings_bulk

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
        r = _http_post(oauth_url, data=payload, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token")
            expires_in = data.get("expires_in", 21600)
            new_expires_at = time.time() + float(expires_in)

            if new_access_token:
                try:
                    to_save = {
                        "ml_access_token": new_access_token,
                        "ml_token_expires_at": str(new_expires_at),
                    }
                    if new_refresh_token:
                        to_save["ml_refresh_token"] = new_refresh_token
                    set_settings_bulk(to_save)
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



def extract_candidate_ids(url: str) -> list[str]:
    """Extrai todos os identificadores MLB possíveis da URL (catálogo, item_id, path)."""
    ids: list[str] = []

    def _add(raw: str):
        cleaned = raw.replace("-", "").strip().upper()
        # Se for MLBU..., adiciona a versão normalizada MLB... e a original
        if cleaned.startswith("MLBU"):
            alt = "MLB" + cleaned[4:]
            if alt not in ids:
                ids.append(alt)
        if cleaned and cleaned not in ids:
            ids.append(cleaned)

    # 1. Catálogo /up/MLBU... ou /p/MLB...
    for m in re.finditer(r"/(?:up/|p/)(MLBU?-?\d{6,14})", url, re.IGNORECASE):
        _add(m.group(1))

    # 2. Parâmetros de consulta (ex.: item_id:MLB5474859082 ou item_id=MLB5474859082)
    for m in re.finditer(r"item_id[:=](MLBU?-?\d{6,14})", url, re.IGNORECASE):
        _add(m.group(1))

    # 3. Path geral /MLB-123456... ou /MLB123456...
    for m in re.finditer(r"(MLB-?\d{6,14})", url, re.IGNORECASE):
        _add(m.group(1))

    return ids


def extract_mlb_id(url: str) -> str | None:
    """Extrai o código principal do item MLB ou catálogo da URL."""
    cids = extract_candidate_ids(url)
    return cids[0] if cids else None


def fetch_from_api(url: str, token: str) -> PriceResult:
    """Consulta a API oficial do Mercado Livre usando os identificadores extraídos da URL."""
    candidate_ids = extract_candidate_ids(url)
    if not candidate_ids:
        log.warning("[ML-API] Não foi possível extrair nenhum código MLB da URL: %s", url)
        return PriceResult(price=None, available=False, source="ml:id-invalido")

    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/json",
    }
    last_status: int | None = None

    for cid in candidate_ids:
        is_catalog = "/p/" in url or "/up/" in url or cid.startswith("MLBU")
        endpoints = (
            [
                f"https://api.mercadolibre.com/products/{cid}",
                f"https://api.mercadolibre.com/products/{cid}/items",
                f"https://api.mercadolibre.com/items?ids={cid}",
                f"https://api.mercadolibre.com/items/{cid}",
            ]
            if is_catalog
            else [
                f"https://api.mercadolibre.com/items?ids={cid}",
                f"https://api.mercadolibre.com/items/{cid}",
                f"https://api.mercadolibre.com/products/{cid}",
                f"https://api.mercadolibre.com/products/{cid}/items",
            ]
        )

        for api_url in endpoints:
            try:
                r = _http_get(api_url, headers=headers, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    price = None
                    available = True
                    name = None

                    # A) Resposta multiget: /items?ids=... -> [{"code": 200, "body": {...}}]
                    if isinstance(data, list) and data and isinstance(data[0], dict) and "body" in data[0]:
                        entry = data[0]
                        if entry.get("code") == 200:
                            body = entry.get("body", {})
                            if body.get("price") is not None:
                                price = float(body["price"])
                                available = body.get("status", "active") == "active" and (
                                    body.get("available_quantity", 1) > 0
                                )
                                name = body.get("title") or body.get("name")
                                log.info(
                                    "[ML-API] Preço R$ %.2f obtido via multiget items (%s)",
                                    price,
                                    api_url,
                                )
                                return PriceResult(
                                    price=price,
                                    available=available,
                                    name=name,
                                    source="ml:api-oficial",
                                    final_price=price,
                                )
                        elif entry.get("code") in (401, 403, 404):
                            last_status = entry.get("code")
                            continue

                    # B) Lista de ofertas (ex.: /products/{id}/items)
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
                                available = item.get("status") == "active" or (
                                    item.get("available_quantity", 0) > 0
                                )
                                name = item.get("title") or name
                                log.info(
                                    "[ML-API] Preço R$ %.2f obtido via lista de ofertas (%s)",
                                    price,
                                    api_url,
                                )
                                return PriceResult(
                                    price=price,
                                    available=available,
                                    name=name,
                                    source="ml:api-oficial",
                                    final_price=price,
                                )

                    # C) Objeto individual (produto de catálogo ou item direto)
                    if isinstance(data, dict):
                        name = data.get("title") or data.get("name")

                        # 1. Buy box winner do produto de catálogo
                        bb = data.get("buy_box_winner")
                        if isinstance(bb, dict):
                            if bb.get("price") is not None:
                                price = float(bb["price"])
                                available = bb.get("status", "active") == "active"
                            winner_item_id = bb.get("item_id") or bb.get("id")
                            if price is None and winner_item_id:
                                item_r = _http_get(
                                    f"https://api.mercadolibre.com/items?ids={winner_item_id}",
                                    headers=headers,
                                    timeout=10,
                                )
                                if item_r.status_code == 200:
                                    w_json = item_r.json()
                                    if (
                                        isinstance(w_json, list)
                                        and w_json
                                        and w_json[0].get("code") == 200
                                    ):
                                        w_body = w_json[0].get("body", {})
                                        if w_body.get("price") is not None:
                                            price = float(w_body["price"])
                                            available = w_body.get("status") == "active" and (
                                                w_body.get("available_quantity", 0) > 0
                                            )
                                            name = w_body.get("title") or name
                        elif isinstance(bb, str) and bb.startswith("MLB"):
                            item_r = _http_get(
                                f"https://api.mercadolibre.com/items?ids={bb}",
                                headers=headers,
                                timeout=10,
                            )
                            if item_r.status_code == 200:
                                w_json = item_r.json()
                                if (
                                    isinstance(w_json, list)
                                    and w_json
                                    and w_json[0].get("code") == 200
                                ):
                                    w_body = w_json[0].get("body", {})
                                    if w_body.get("price") is not None:
                                        price = float(w_body["price"])
                                        available = w_body.get("status") == "active" and (
                                            w_body.get("available_quantity", 0) > 0
                                        )
                                        name = w_body.get("title") or name

                        # 2. Anúncio direto (data['price'])
                        if price is None and data.get("price") is not None:
                            price = float(data["price"])
                            available = data.get("status", "active") == "active" and (
                                data.get("available_quantity", 1) > 0
                            )

                        # 3. Variações (pickers)
                        if price is None and "pickers" in data and isinstance(data["pickers"], list):
                            for picker in data["pickers"]:
                                for prod in picker.get("products", []):
                                    prod_bb = prod.get("buy_box_winner")
                                    if isinstance(prod_bb, dict) and prod_bb.get("price") is not None:
                                        price = float(prod_bb["price"])
                                        available = prod_bb.get("status", "active") == "active"
                                        break
                                if price is not None:
                                    break

                        # 4. Children IDs
                        if price is None and data.get("children_ids"):
                            for child_id in data["children_ids"][:3]:
                                child_r = _http_get(
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

                    if price is not None:
                        log.info(
                            "[ML-API] Preço R$ %.2f obtido com sucesso via API oficial (%s)",
                            price,
                            api_url,
                        )
                        return PriceResult(
                            price=price,
                            available=available,
                            name=name,
                            source="ml:api-oficial",
                            final_price=price,
                        )
                elif r.status_code == 401:
                    last_status = 401
                    log.warning("[ML-API] HTTP 401 Unauthorized: token inválido ou expirado.")
                    try:
                        from ..db import set_setting

                        set_setting("ml_token_expires_at", "0")
                    except Exception:
                        pass
                    break
                elif r.status_code in (403, 404):
                    last_status = r.status_code
                    log.warning("[ML-API] HTTP %s para %s", r.status_code, api_url)
                else:
                    last_status = r.status_code
                    log.info("[ML-API] Status HTTP %s para %s", r.status_code, api_url)
            except Exception as exc:
                log.warning("[ML-API] Erro ao conectar na API (%s): %s", api_url, exc)

    error_source = f"ml:api-{last_status}" if last_status else "ml:sem-oferta-ativa"
    log.warning("[ML-API] Falha ao obter preço para %s. Origem do erro: %s", url, error_source)
    return PriceResult(price=None, available=False, source=error_source)


def scrape_ml(url: str) -> PriceResult:
    """Consulta exclusiva via API oficial do Mercado Livre (sem scraping HTTP)."""
    token = get_access_token()
    if not token:
        log.warning("[ML-API] Consulta cancelada: nenhum token OAuth disponível.")
        return PriceResult(price=None, available=False, source="ml:sem-token")

    return fetch_from_api(url, token)


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


def search_mercadolivre_offers(keyword: str, limit: int = 5) -> list[dict]:
    """Busca ofertas no Mercado Livre via API oficial (catálogo /products/search com fallback para /sites/MLB/search)."""
    if not keyword or not keyword.strip():
        return []

    token = get_access_token()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    results: list[dict] = []
    # 1. Tenta buscar em /products/search (produtos de catálogo com BuyBox)
    try:
        url_products = "https://api.mercadolibre.com/products/search"
        params_products = {
            "status": "active",
            "site_id": "MLB",
            "q": keyword.strip(),
            "limit": limit,
        }
        resp = _http_get(url_products, params=params_products, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("results", []):
                prod_id = item.get("id")
                name = item.get("name")
                permalink = item.get("permalink") or (f"https://www.mercadolivre.com.br/p/{prod_id}" if prod_id else "")
                if not permalink or not name:
                    continue
                price = None
                bb = item.get("buy_box_winner")
                if bb and bb.get("price") is not None:
                    price = parse_brl(bb.get("price"))
                results.append({
                    "store": "Mercado Livre",
                    "title": name.strip(),
                    "url": permalink.strip(),
                    "price": price,
                    "source": "ml:api",
                })
    except Exception as exc:
        log.warning("Erro ao buscar em /products/search do Mercado Livre para '%s': %s", keyword, exc)

    if results:
        return results[:limit]

    # 2. Fallback: /sites/MLB/search (anúncios gerais)
    try:
        url_search = "https://api.mercadolibre.com/sites/MLB/search"
        params_search = {"q": keyword.strip(), "limit": limit}
        resp = _http_get(url_search, params=params_search, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("results", []):
                title = item.get("title")
                permalink = item.get("permalink")
                if not permalink or not title:
                    continue
                raw_price = item.get("price")
                price = parse_brl(raw_price) if raw_price is not None else None
                results.append({
                    "store": "Mercado Livre",
                    "title": title.strip(),
                    "url": permalink.strip(),
                    "price": price,
                    "source": "ml:api",
                })
    except Exception as exc:
        log.warning("Erro ao buscar em /sites/MLB/search do Mercado Livre para '%s': %s", keyword, exc)

    return results[:limit]
