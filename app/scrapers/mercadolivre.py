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



def _is_ml_offer_available(item: dict) -> bool:
    """Verifica se um item/oferta retornado pela API do Mercado Livre está disponível.

    Endpoints como /products/{id}/items não retornam 'status' nem 'available_quantity'
    (pois listam apenas ofertas ativas), e /items pode omitir 'available_quantity'
    em anúncios de terceiros.
    """
    if not isinstance(item, dict):
        return False
    status = item.get("status")
    if status is not None and str(status).strip().lower() != "active":
        return False
    qty = item.get("available_quantity")
    if qty is not None:
        try:
            if int(qty) <= 0:
                return False
        except (ValueError, TypeError):
            pass
    return True


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


def _fetch_catalog_html_price(url: str) -> PriceResult | None:
    """Tenta baixar o HTML público de url do Mercado Livre e extrair o preço."""
    from .base import HEADERS as DESKTOP_HEADERS

    for hdrs in (DESKTOP_HEADERS, ML_HEADERS):
        try:
            r_html = _http_get(url, headers=hdrs, timeout=6, follow_redirects=True)
            if r_html.status_code == 200 and getattr(r_html, "text", None):
                html_res = parse(r_html.text)
                if html_res and html_res.price is not None:
                    return html_res
        except Exception as h_exc:
            log.debug("[ML-API] Falha no fallback HTML com headers para %s: %s", url, h_exc)
    return None


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
    catalog_found = False

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
                                available = _is_ml_offer_available(body)
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
                                available = _is_ml_offer_available(item)
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
                        catalog_name = data.get("name") or data.get("title")
                        if "/products/" in api_url:
                            catalog_found = True

                        # 1. Buy box winner do produto de catálogo
                        bb = data.get("buy_box_winner")
                        if isinstance(bb, dict):
                            if bb.get("price") is not None:
                                price = float(bb["price"])
                                available = _is_ml_offer_available(bb)
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
                                            available = _is_ml_offer_available(w_body)
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
                                        available = _is_ml_offer_available(w_body)
                                        name = w_body.get("title") or name

                        # 2. Anúncio direto (data['price'])
                        if price is None and data.get("price") is not None:
                            price = float(data["price"])
                            available = _is_ml_offer_available(data)

                        # 3. Variações (pickers)
                        if price is None and "pickers" in data and isinstance(data["pickers"], list):
                            for picker in data["pickers"]:
                                for prod in picker.get("products", []):
                                    prod_bb = prod.get("buy_box_winner")
                                    if isinstance(prod_bb, dict) and prod_bb.get("price") is not None:
                                        price = float(prod_bb["price"])
                                        available = _is_ml_offer_available(prod_bb)
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
                                        available = _is_ml_offer_available(c_bb)
                                        name = child_data.get("title") or name
                                        break

                        # Passos 5, 6 e 7 para produtos de catálogo sem preço
                        if price is None and ("/products/" in api_url or is_catalog):
                            # Passo 5: Busca de itens ativos do catálogo via /sites/MLB/search?product_id={cid}
                            if cid:
                                try:
                                    s_url = "https://api.mercadolibre.com/sites/MLB/search"
                                    s_params = {"product_id": cid, "limit": 5}
                                    s_resp = _http_get(s_url, params=s_params, headers=headers, timeout=10)
                                    if s_resp.status_code == 200:
                                        s_data = s_resp.json()
                                        active_items = []
                                        for sit in s_data.get("results", []):
                                            sp = sit.get("price")
                                            if sp is not None:
                                                active_items.append((float(sp), sit.get("title")))
                                        if active_items:
                                            active_items.sort(key=lambda x: x[0])
                                            price = active_items[0][0]
                                            name = active_items[0][1] or name or catalog_name
                                            available = True
                                            log.info(
                                                "[ML-API] Preço R$ %.2f obtido via /sites/MLB/search?product_id=%s",
                                                price,
                                                cid,
                                            )
                                except Exception as s_exc:
                                    log.warning("[ML-API] Erro ao buscar por product_id %s: %s", cid, s_exc)

                            # Passo 6: Busca por /products/search ou /sites/MLB/search com catalog_name
                            if price is None and catalog_name:
                                try:
                                    q_url = "https://api.mercadolibre.com/products/search"
                                    q_params = {
                                        "status": "active",
                                        "site_id": "MLB",
                                        "q": catalog_name.strip(),
                                        "limit": 5,
                                    }
                                    q_resp = _http_get(q_url, params=q_params, headers=headers, timeout=10)
                                    if q_resp.status_code == 200:
                                        q_data = q_resp.json()
                                        for q_item in q_data.get("results", []):
                                            q_bb = q_item.get("buy_box_winner")
                                            if isinstance(q_bb, dict) and q_bb.get("price") is not None:
                                                price = float(q_bb["price"])
                                                name = q_item.get("name") or name or catalog_name
                                                available = True
                                                log.info(
                                                    "[ML-API] Preço R$ %.2f obtido via /products/search com catalog_name '%s'",
                                                    price,
                                                    catalog_name,
                                                )
                                                break

                                    if price is None:
                                        qs_url = "https://api.mercadolibre.com/sites/MLB/search"
                                        qs_params = {"q": catalog_name.strip(), "limit": 5}
                                        qs_resp = _http_get(qs_url, params=qs_params, headers=headers, timeout=10)
                                        if qs_resp.status_code == 200:
                                            qs_data = qs_resp.json()
                                            qs_items = []
                                            for qsit in qs_data.get("results", []):
                                                qsp = qsit.get("price")
                                                if qsp is not None:
                                                    qs_items.append((float(qsp), qsit.get("title")))
                                            if qs_items:
                                                qs_items.sort(key=lambda x: x[0])
                                                price = qs_items[0][0]
                                                name = qs_items[0][1] or name or catalog_name
                                                available = True
                                                log.info(
                                                    "[ML-API] Preço R$ %.2f obtido via /sites/MLB/search com catalog_name '%s'",
                                                    price,
                                                    catalog_name,
                                                )
                                except Exception as q_exc:
                                    log.warning("[ML-API] Erro ao buscar por catalog_name %s: %s", catalog_name, q_exc)

                            # Passo 7: Fallback de HTML público da página de catálogo (/p/MLB... ou /up/MLBU...)
                            if price is None and ("/p/" in url or "/up/" in url or is_catalog):
                                html_res = _fetch_catalog_html_price(url)
                                if html_res and html_res.price is not None:
                                    log.info(
                                        "[ML-API] Preço R$ %.2f obtido via fallback HTML da página de catálogo (%s)",
                                        html_res.price,
                                        url,
                                    )
                                    return html_res

                    if price is not None:
                        log.info(
                            "[ML-API] Preço R$ %.2f obtido com sucesso via API oficial (%s)",
                            price,
                            api_url,
                        )
                        return PriceResult(
                            price=price,
                            available=available,
                            name=name or catalog_name,
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

    # Se após tentar todas as opções de API não encontrou preço, tenta fallback de HTML público da página
    html_res = _fetch_catalog_html_price(url)
    if html_res and html_res.price is not None:
        log.info(
            "[ML-API] Preço R$ %.2f obtido via fallback HTML final (%s)",
            html_res.price,
            url,
        )
        return html_res

    error_source = (
        "ml:sem-oferta-ativa"
        if catalog_found
        else (f"ml:api-{last_status}" if last_status else "ml:sem-oferta-ativa")
    )
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
            gen_available = gen.available
            if not gen_available and not out_of_stock_el and "OutOfStock" not in html:
                gen_available = True
            return PriceResult(
                price=gen.price,
                available=available and gen_available,
                name=name or gen.name,
                source=gen.source,
                coupon_code=gen.coupon_code,
                final_price=gen.final_price or gen.price,
            )

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


def _is_valid_br_ml_item(item: dict, permalink: str) -> bool:
    """Verifica se um item retornado nas buscas do Mercado Livre é válido para o Brasil.

    Descarta links estrangeiros, moedas diferentes de BRL e itens de compra internacional (CBT).
    """
    if not permalink:
        return False
    parsed = urlparse(permalink)
    hostname = (parsed.hostname or "").lower()
    if not (hostname == "mercadolivre.com.br" or hostname.endswith(".mercadolivre.com.br")):
        return False

    curr = item.get("currency_id")
    if curr and str(curr).strip().upper() != "BRL":
        return False

    bb = item.get("buy_box_winner")
    if isinstance(bb, dict):
        bb_curr = bb.get("currency_id")
        if bb_curr and str(bb_curr).strip().upper() != "BRL":
            return False

    intl_mode = item.get("international_delivery_mode")
    if intl_mode is not None and str(intl_mode).strip().lower() not in ("", "none"):
        return False

    tags = item.get("tags")
    if isinstance(tags, list) and "cbt_item" in tags:
        return False

    return True


def _enrich_unpriced_item(unp: dict, headers: dict) -> float | None:
    """Tenta obter o preço de um item de catálogo unpriced através de múltiplos endpoints em cascata."""
    enriched_price = None
    p_id = unp.get("prod_id") or extract_mlb_id(unp.get("url", ""))
    winner_id = unp.get("winner_item_id")

    # 1. Consulta /products/{p_id} (buy_box_winner, pickers)
    if p_id:
        try:
            p_resp = _http_get(f"https://api.mercadolibre.com/products/{p_id}", headers=headers, timeout=5)
            if p_resp.status_code == 200:
                p_data = p_resp.json()
                bb = p_data.get("buy_box_winner")
                if isinstance(bb, dict):
                    p_winner = bb.get("item_id") or bb.get("id")
                    if p_winner:
                        winner_id = p_winner
                        unp["winner_item_id"] = winner_id
                        if "item_id" not in unp.get("url", ""):
                            sep = "&" if "?" in unp["url"] else "?"
                            unp["url"] = f"{unp['url']}{sep}pdp_filters=item_id:{winner_id}"
                    if bb.get("price") is not None and _is_ml_offer_available(bb):
                        enriched_price = parse_brl(bb.get("price"))
                elif "pickers" in p_data and isinstance(p_data["pickers"], list):
                    for picker in p_data["pickers"]:
                        for prod in picker.get("products", []):
                            prod_bb = prod.get("buy_box_winner")
                            if isinstance(prod_bb, dict):
                                prod_winner = prod_bb.get("item_id") or prod_bb.get("id")
                                if prod_winner and not winner_id:
                                    winner_id = prod_winner
                                    unp["winner_item_id"] = winner_id
                                    if "item_id" not in unp.get("url", ""):
                                        sep = "&" if "?" in unp["url"] else "?"
                                        unp["url"] = f"{unp['url']}{sep}pdp_filters=item_id:{winner_id}"
                                if prod_bb.get("price") is not None and _is_ml_offer_available(prod_bb):
                                    enriched_price = parse_brl(prod_bb.get("price"))
                                    break
                        if enriched_price is not None:
                            break
        except Exception as exc:
            log.debug("Erro ao consultar /products/%s para enriquecimento: %s", p_id, exc)

    # 2. Consulta /items?ids={winner_id}
    if enriched_price is None and winner_id:
        try:
            w_resp = _http_get(
                f"https://api.mercadolibre.com/items?ids={winner_id}",
                headers=headers,
                timeout=5,
            )
            if w_resp.status_code == 200:
                w_json = w_resp.json()
                if (
                    isinstance(w_json, list)
                    and w_json
                    and w_json[0].get("code") == 200
                ):
                    w_body = w_json[0].get("body", {})
                    if (
                        w_body.get("price") is not None
                        and _is_ml_offer_available(w_body)
                        and _is_valid_br_ml_item(w_body, unp.get("url", ""))
                    ):
                        enriched_price = parse_brl(w_body["price"])
                        if "item_id" not in unp.get("url", ""):
                            sep = "&" if "?" in unp["url"] else "?"
                            unp["url"] = f"{unp['url']}{sep}pdp_filters=item_id:{winner_id}"
        except Exception as exc:
            log.debug("Erro ao consultar /items?ids=%s para enriquecimento: %s", winner_id, exc)

    # 3. Consulta /products/{p_id}/items (ofertas ativas do catálogo)
    if enriched_price is None and p_id:
        try:
            it_resp = _http_get(
                f"https://api.mercadolibre.com/products/{p_id}/items",
                headers=headers,
                timeout=5,
            )
            if it_resp.status_code == 200:
                it_data = it_resp.json()
                it_list = (
                    it_data
                    if isinstance(it_data, list)
                    else (it_data.get("results", []) if isinstance(it_data, dict) else [])
                )
                for item in it_list:
                    if (
                        item.get("price") is not None
                        and _is_ml_offer_available(item)
                        and _is_valid_br_ml_item(item, unp.get("url", ""))
                    ):
                        enriched_price = parse_brl(item["price"])
                        item_id = item.get("item_id") or item.get("id")
                        if item_id:
                            unp["winner_item_id"] = item_id
                            if "item_id" not in unp.get("url", ""):
                                sep = "&" if "?" in unp["url"] else "?"
                                unp["url"] = f"{unp['url']}{sep}pdp_filters=item_id:{item_id}"
                        break
        except Exception as exc:
            log.debug("Erro ao consultar /products/%s/items para enriquecimento: %s", p_id, exc)

    # 4. Consulta /sites/MLB/search?product_id={p_id}&limit=5
    if enriched_price is None and p_id:
        try:
            s_resp = _http_get(
                f"https://api.mercadolibre.com/sites/MLB/search?product_id={p_id}&limit=5",
                headers=headers,
                timeout=5,
            )
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                active_items = []
                for sit in s_data.get("results", []):
                    sp = sit.get("price")
                    if (
                        sp is not None
                        and _is_ml_offer_available(sit)
                        and _is_valid_br_ml_item(sit, sit.get("permalink") or unp.get("url", ""))
                    ):
                        p_val = parse_brl(sp)
                        if p_val is not None:
                            active_items.append((p_val, sit.get("id") or sit.get("item_id")))
                if active_items:
                    active_items.sort(key=lambda x: x[0])
                    enriched_price = active_items[0][0]
                    best_id = active_items[0][1]
                    if best_id:
                        unp["winner_item_id"] = best_id
                        if "item_id" not in unp.get("url", ""):
                            sep = "&" if "?" in unp["url"] else "?"
                            unp["url"] = f"{unp['url']}{sep}pdp_filters=item_id:{best_id}"
        except Exception as exc:
            log.debug("Erro ao consultar /sites/MLB/search?product_id=%s para enriquecimento: %s", p_id, exc)

    # 5. Fallback HTML da página de catálogo
    if enriched_price is None and unp.get("url"):
        try:
            h_res = _fetch_catalog_html_price(unp["url"])
            if h_res and h_res.price is not None and h_res.available:
                enriched_price = h_res.price
        except Exception as exc:
            log.debug("Erro no fallback HTML de enriquecimento para %s: %s", unp["url"], exc)

    return enriched_price


def search_mercadolivre_offers(keyword: str, limit: int = 5) -> list[dict]:
    """Busca ofertas no Mercado Livre via API oficial (catálogo /products/search com fallback para /sites/MLB/search)."""
    if not keyword or not keyword.strip():
        return []

    token = get_access_token()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    priced_results: list[dict] = []
    unpriced_results: list[dict] = []

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
                if not _is_valid_br_ml_item(item, permalink):
                    continue
                price = None
                winner_item_id = None
                bb = item.get("buy_box_winner")
                if isinstance(bb, dict):
                    winner_item_id = bb.get("item_id") or bb.get("id")
                    if winner_item_id and "item_id" not in permalink:
                        sep = "&" if "?" in permalink else "?"
                        permalink = f"{permalink}{sep}pdp_filters=item_id:{winner_item_id}"
                    if bb.get("price") is not None:
                        price = parse_brl(bb.get("price"))
                elif bb and hasattr(bb, "get") and bb.get("price") is not None:
                    price = parse_brl(bb.get("price"))

                entry = {
                    "store": "Mercado Livre",
                    "title": name.strip(),
                    "url": permalink.strip(),
                    "price": price,
                    "source": "ml:api",
                    "prod_id": prod_id,
                }
                if winner_item_id:
                    entry["winner_item_id"] = winner_item_id

                if price is not None:
                    priced_results.append(entry)
                else:
                    unpriced_results.append(entry)
    except Exception as exc:
        log.warning("Erro ao buscar em /products/search do Mercado Livre para '%s': %s", keyword, exc)

    # 2. Se não atingiu o limite com itens precificados, busca em /sites/MLB/search (anúncios gerais)
    if len(priced_results) < limit:
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
                    if not _is_valid_br_ml_item(item, permalink):
                        continue
                    raw_price = item.get("price")
                    price = parse_brl(raw_price) if raw_price is not None else None
                    entry = {
                        "store": "Mercado Livre",
                        "title": title.strip(),
                        "url": permalink.strip(),
                        "price": price,
                        "source": "ml:api",
                    }
                    if price is not None:
                        if not any(r["url"] == entry["url"] for r in priced_results):
                            priced_results.append(entry)
                    else:
                        if not any(r["url"] == entry["url"] for r in unpriced_results):
                            unpriced_results.append(entry)
        except Exception as exc:
            log.warning("Erro ao buscar em /sites/MLB/search do Mercado Livre para '%s': %s", keyword, exc)

    # 2.5 Enriquecimento de preços para itens unpriced
    if unpriced_results and len(priced_results) < limit:
        to_enrich_count = min(limit - len(priced_results), len(unpriced_results))
        remaining_unpriced: list[dict] = []
        for idx, unp in enumerate(unpriced_results):
            if idx < to_enrich_count:
                enriched_price = _enrich_unpriced_item(unp, headers)
                if enriched_price is not None:
                    unp["price"] = enriched_price
                    unp["source"] = "ml:api-enriched"
                    if not any(r["url"] == unp["url"] for r in priced_results):
                        priced_results.append(unp)
                else:
                    remaining_unpriced.append(unp)
            else:
                remaining_unpriced.append(unp)
        unpriced_results = remaining_unpriced

    # 2.6 Retry com query simplificada quando termos muito específicos não retornam resultados
    words = keyword.strip().split()
    if not priced_results and len(words) >= 4:
        if len(words) > 4:
            simplified_kw = " ".join(words[:4])
        else:
            simplified_kw = " ".join(words[:3])

        if simplified_kw and simplified_kw.lower() != keyword.strip().lower():
            log.info(
                "Nenhum resultado para query '%s'. Tentando retry simplificado com '%s'",
                keyword.strip(),
                simplified_kw,
            )
            retry_unpriced: list[dict] = []
            try:
                url_products = "https://api.mercadolibre.com/products/search"
                params_products = {
                    "status": "active",
                    "site_id": "MLB",
                    "q": simplified_kw,
                    "limit": limit,
                }
                resp = _http_get(url_products, params=params_products, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", []):
                        prod_id = item.get("id")
                        name = item.get("name")
                        permalink = item.get("permalink") or (
                            f"https://www.mercadolivre.com.br/p/{prod_id}" if prod_id else ""
                        )
                        if not permalink or not name:
                            continue
                        if not _is_valid_br_ml_item(item, permalink):
                            continue
                        price = None
                        winner_item_id = None
                        bb = item.get("buy_box_winner")
                        if isinstance(bb, dict):
                            winner_item_id = bb.get("item_id") or bb.get("id")
                            if winner_item_id and "item_id" not in permalink:
                                sep = "&" if "?" in permalink else "?"
                                permalink = f"{permalink}{sep}pdp_filters=item_id:{winner_item_id}"
                            if bb.get("price") is not None:
                                price = parse_brl(bb.get("price"))
                        elif bb and hasattr(bb, "get") and bb.get("price") is not None:
                            price = parse_brl(bb.get("price"))

                        entry = {
                            "store": "Mercado Livre",
                            "title": name.strip(),
                            "url": permalink.strip(),
                            "price": price,
                            "source": "ml:api",
                            "prod_id": prod_id,
                        }
                        if winner_item_id:
                            entry["winner_item_id"] = winner_item_id

                        if price is not None:
                            priced_results.append(entry)
                        else:
                            retry_unpriced.append(entry)
            except Exception as exc:
                log.warning("Erro no retry simplificado de /products/search para '%s': %s", simplified_kw, exc)

            if retry_unpriced and len(priced_results) < limit:
                to_enrich_retry = min(limit - len(priced_results), len(retry_unpriced))
                for idx, unp in enumerate(retry_unpriced):
                    if idx < to_enrich_retry:
                        enriched_price = _enrich_unpriced_item(unp, headers)
                        if enriched_price is not None:
                            unp["price"] = enriched_price
                            unp["source"] = "ml:api-enriched"
                            if not any(r["url"] == unp["url"] for r in priced_results):
                                priced_results.append(unp)

    # 3. Fallback: Se não obteve nenhum item com preço, busca no portal público lista.mercadolivre.com.br
    if not priced_results:
        skip_pub = "PYTEST_CURRENT_TEST" in os.environ and getattr(httpx.get, "__module__", "") == "httpx"
        if not skip_pub:
            try:
                from .base import HEADERS as DESKTOP_HEADERS

                slug = re.sub(r"[^a-zA-Z0-9]+", "-", keyword.strip().lower()).strip("-")
                pub_url = f"https://lista.mercadolivre.com.br/{slug}"
                r_pub = _http_get(pub_url, headers=DESKTOP_HEADERS, timeout=6, follow_redirects=True)
                if r_pub.status_code == 200 and getattr(r_pub, "text", None):
                    soup = BeautifulSoup(r_pub.text, "html.parser")
                    cards = soup.select(".ui-search-layout__item, .poly-card, li.ui-search-layout__item")
                    for card in cards:
                        if len(priced_results) >= limit:
                            break
                        title_el = card.select_one(".poly-component__title, .ui-search-item__title, h2")
                        link_el = card.select_one("a.poly-component__title, a.ui-search-link, a")
                        if not title_el or not link_el or not link_el.get("href"):
                            continue
                        title = title_el.get_text(strip=True)
                        raw_href = link_el["href"].strip()
                        if not raw_href.startswith("http"):
                            continue

                        # Filtra links de rastreamento de anúncios que não apontam para página de produto
                        parsed_h = urlparse(raw_href)
                        h_host = (parsed_h.hostname or "").lower()
                        if "click1.mercadolivre.com.br" in h_host or "mclics" in raw_href:
                            m = re.search(
                                r"(https?://(?:www\.|produto\.)?mercadolivre\.com\.br/(?:p/MLB|MLB-|\w+/[^&]+MLB)[^&\"'>\s]*)",
                                raw_href,
                            )
                            if m:
                                raw_href = m.group(1)
                                parsed_h = urlparse(raw_href)
                                h_host = (parsed_h.hostname or "").lower()
                            else:
                                continue

                        if not (
                            h_host == "mercadolivre.com.br"
                            or h_host == "produto.mercadolivre.com.br"
                            or h_host.endswith(".mercadolivre.com.br")
                        ):
                            continue
                        if "click1" in h_host or "mclics" in raw_href:
                            continue

                        href = raw_href.split("?")[0].strip()

                        price_el = card.select_one(".andes-money-amount__fraction")
                        if not price_el:
                            continue
                        price_val = price_el.get_text(strip=True)
                        cents_el = card.select_one(".andes-money-amount__cents")
                        if cents_el:
                            price_val += "," + cents_el.get_text(strip=True)
                        card_price = parse_brl(price_val)
                        if card_price is not None and title:
                            entry_pub = {
                                "store": "Mercado Livre",
                                "title": title,
                                "url": href,
                                "price": card_price,
                                "source": "ml:public-search",
                            }
                            if not any(r["url"] == entry_pub["url"] for r in priced_results):
                                priced_results.append(entry_pub)
            except Exception as pub_exc:
                log.warning("Erro no fallback de busca pública do Mercado Livre: %s", pub_exc)

    return priced_results[:limit]
