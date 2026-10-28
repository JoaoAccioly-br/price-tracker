"""Módulo de Inteligência Artificial com Google AI Studio (Gemini).

Oferece:
1. Descoberta inteligente de links de lojas para produtos (Search Grounding + APIs de lojas + curadoria).
2. Auto-recuperação de links expirados ou fora de estoque (Self-Healing Links).
"""
import json
import logging
import os
import re
from urllib.parse import urlparse

import httpx

from .scrapers import AMAZON_HEADERS, scrape
from .scrapers.base import PriceResult, UnsafeURLError, canonicalize_url, parse_brl, validate_url_host
from .scrapers.mercadolivre import search_mercadolivre_offers
from .scrapers.shopee import search_shopee_offers

log = logging.getLogger("ai")

_AI_HTTP_CLIENT: httpx.Client | None = None

KNOWN_STORE_DOMAINS = {
    "amazon.com.br": "Amazon",
    "mercadolivre.com.br": "Mercado Livre",
    "shopee.com.br": "Shopee",
    "kabum.com.br": "KaBuM!",
    "magazineluiza.com.br": "Magalu",
    "aliexpress.com": "AliExpress",
    "dell.com": "Dell",
    "lenovo.com": "Lenovo",
}


def _get_ai_client() -> httpx.Client:
    global _AI_HTTP_CLIENT
    if _AI_HTTP_CLIENT is None or _AI_HTTP_CLIENT.is_closed:
        _AI_HTTP_CLIENT = httpx.Client(timeout=15.0)
    return _AI_HTTP_CLIENT


def _http_post(url: str, **kwargs):
    if getattr(httpx.post, "__module__", "") != "httpx":
        return httpx.post(url, **kwargs)
    return _get_ai_client().post(url, **kwargs)


def _http_get(url: str, **kwargs):
    if getattr(httpx.get, "__module__", "") != "httpx":
        return httpx.get(url, **kwargs)
    return _get_ai_client().get(url, **kwargs)


def get_gemini_api_key() -> str:
    """Retorna a chave da API do Gemini configurada no ambiente."""
    return (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )


_ACTIVE_GEMINI_MODEL: str | None = None
FALLBACK_GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]


def get_gemini_model() -> str:
    """Retorna o modelo Gemini configurado (padrão: gemini-3.5-flash-lite)."""
    global _ACTIVE_GEMINI_MODEL
    if _ACTIVE_GEMINI_MODEL:
        return _ACTIVE_GEMINI_MODEL
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"


def _call_gemini_generate_content(
    headers: dict,
    payload: dict,
    timeout: float = 8.0,
) -> httpx.Response | None:
    """Executa POST em generateContent com auto-fallback inteligente caso ocorra erro (404, 429, 500, 503 ou timeout)."""
    global _ACTIVE_GEMINI_MODEL

    initial_model = get_gemini_model().removeprefix("models/").strip()
    tried_models: set[str] = set()

    def _do_post(model_name: str) -> httpx.Response | None:
        clean = model_name.removeprefix("models/").strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean}:generateContent"
        return _http_post(url, headers=headers, json=payload, timeout=timeout)

    tried_models.add(initial_model)
    resp = None
    try:
        resp = _do_post(initial_model)
    except Exception as exc:
        log.warning("Erro ao chamar Gemini (%s): %s", initial_model, exc)

    if resp is not None and resp.status_code == 200:
        return resp

    should_fallback = (resp is None) or (resp.status_code in (404, 429, 500, 503))
    if not should_fallback:
        return resp

    status_desc = resp.status_code if resp is not None else "timeout/exception"
    log.warning(
        "Gemini falhou com status %s para modelo '%s'. Iniciando auto-fallback...",
        status_desc,
        initial_model,
    )
    candidates: list[str] = []

    # 1. Sugestão da própria mensagem de erro da API do Google
    if resp is not None and getattr(resp, "text", None):
        m = re.search(r"use\s+(?:models/)?([a-zA-Z0-9._-]+)", resp.text, re.IGNORECASE)
        if m:
            suggested = m.group(1).removeprefix("models/").strip()
            if suggested and suggested not in tried_models:
                candidates.append(suggested)

    # 2. Lista de modelos de fallback em ordem
    for fb in FALLBACK_GEMINI_MODELS:
        clean_fb = fb.removeprefix("models/").strip()
        if clean_fb not in tried_models and clean_fb not in candidates:
            candidates.append(clean_fb)

    for cand in candidates:
        tried_models.add(cand)
        try:
            cand_resp = _do_post(cand)
            if cand_resp is not None and cand_resp.status_code == 200:
                log.info("Auto-fallback do Gemini bem-sucedido: '%s' -> '%s'", initial_model, cand)
                _ACTIVE_GEMINI_MODEL = cand
                return cand_resp
            sc = cand_resp.status_code if cand_resp is not None else "timeout"
            log.warning(
                "Fallback Gemini '%s' retornou HTTP %s",
                cand,
                sc,
            )
        except Exception as exc:
            log.warning("Erro ao tentar fallback Gemini com '%s': %s", cand, exc)

    return resp


def _detect_store_name(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        for domain, name in KNOWN_STORE_DOMAINS.items():
            if host == domain or host.endswith(f".{domain}"):
                return name
        return host.removeprefix("www.")
    except Exception:
        return "Loja"


def _is_brazilian_store_url(url: str) -> bool:
    """Verifica se a URL pertence a uma loja/marketplace operando no Brasil."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False

    if not parsed.hostname:
        return False

    host = parsed.hostname.lower().rstrip(".")
    path = (parsed.path or "").lower()

    # Rejeita domínios estrangeiros conhecidos
    foreign_domains = (
        "mercadolibre.",
        "ebay.",
        "walmart.",
        "bestbuy.",
        "newegg.",
        "target.com",
        "bhphotovideo.",
    )
    if any(fd in host for fd in foreign_domains):
        return False

    # Validação de marketplaces com TLD específico por país
    if "amazon." in host and not host.endswith("amazon.com.br"):
        return False
    if "shopee." in host and not host.endswith("shopee.com.br"):
        return False
    if "mercadolivre." in host and not host.endswith("mercadolivre.com.br"):
        return False

    # Fabricantes e marketplaces globais aceitos
    global_allowed = ("dell.com", "lenovo.com", "aliexpress.com")
    if any(host == gh or host.endswith("." + gh) for gh in global_allowed):
        if re.search(r"/(?:en-[a-z]{2}|es-[a-z]{2}|fr-[a-z]{2}|de-[a-z]{2}|it-[a-z]{2})(/|$)", path):
            return False
        return True

    # Para qualquer outro domínio, exige terminação .br
    return host.endswith(".br")


def _gemini_grounded_search(
    product_name: str,
    category: str = "Notebook",
    target_store: str | None = None,
) -> list[dict]:
    """Usa o Google Search Grounding do Gemini para descobrir URLs reais do produto na web."""
    api_key = get_gemini_api_key()
    if not api_key:
        return []

    if target_store:
        store_filter_hint = (
            f"especificamente na loja {target_store} do Brasil (domínio brasileiro .com.br, com preços em Reais R$)"
        )
    else:
        store_filter_hint = (
            "em lojas brasileiras confiáveis (.com.br, como Amazon Brasil, KaBuM, Magazine Luiza, Mercado Livre, Shopee)"
        )
    prompt = (
        f"Encontre links diretos de páginas de compra ativas e em estoque do produto '{product_name}' "
        f"(Categoria: {category}) {store_filter_hint}. "
        "Exija apenas páginas de produtos ativos e em estoque no Brasil (em Reais R$), descartando produtos esgotados, indisponíveis ou de sites internacionais. "
        "Retorne links exatos e completos das páginas do produto (não use links de páginas de busca geral). "
        "Inclua obrigatoriamente as URLs completas (https://...) diretamente no texto da resposta, além dos chunks de grounding."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    found_candidates: list[dict] = []
    seen_urls: set[str] = set()

    try:
        resp = _call_gemini_generate_content(headers=headers, payload=payload, timeout=12.0)
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp else "sem-resposta"
            text = resp.text[:200] if resp else ""
            log.warning("Gemini Grounded Search falhou (%s): %s", status, text)
            return []

        data = resp.json()
        candidate_obj = (data.get("candidates") or [{}])[0]

        # 1. Extrai dos chunks de grounding (fontes reais de busca)
        chunks = candidate_obj.get("groundingMetadata", {}).get("groundingChunks", [])
        for ch in chunks:
            web = ch.get("web", {})
            uri = web.get("uri") or ""
            title = web.get("title") or product_name
            if uri.startswith(("http://", "https://")):
                if "vertexaisearch.cloud.google.com/grounding-api-redirect/" in uri:
                    try:
                        r_red = _http_get(uri, timeout=4.0, follow_redirects=True)
                        if r_red and r_red.url:
                            resolved = str(r_red.url)
                            if resolved.startswith(("http://", "https://")):
                                uri = resolved
                    except Exception as red_exc:
                        log.debug("Erro ao resolver redirect de grounding '%s': %s", uri, red_exc)

                clean_c = canonicalize_url(uri)
                if clean_c not in seen_urls:
                    seen_urls.add(clean_c)
                    store = _detect_store_name(uri)
                    found_candidates.append({
                        "store": store,
                        "title": title.strip(),
                        "url": uri.strip(),
                        "price": None,
                        "source": "gemini:grounded",
                    })

        # 2. Extrai de URLs presentes no texto gerado
        parts = candidate_obj.get("content", {}).get("parts", [])
        for p in parts:
            text = p.get("text", "")
            matches = re.findall(r"https?://[^\s)\]\"'>]+", text)
            for m_url in matches:
                if "vertexaisearch.cloud.google.com/grounding-api-redirect/" in m_url:
                    try:
                        r_red = _http_get(m_url, timeout=4.0, follow_redirects=True)
                        if r_red and r_red.url:
                            resolved = str(r_red.url)
                            if resolved.startswith(("http://", "https://")):
                                m_url = resolved
                    except Exception as red_exc:
                        log.debug("Erro ao resolver redirect de texto '%s': %s", m_url, red_exc)

                clean_c = canonicalize_url(m_url)
                if clean_c not in seen_urls:
                    seen_urls.add(clean_c)
                    store = _detect_store_name(m_url)
                    found_candidates.append({
                        "store": store,
                        "title": product_name,
                        "url": m_url.strip(),
                        "price": None,
                        "source": "gemini:text",
                    })

    except Exception as exc:
        log.warning("Erro ao executar Grounded Search no Gemini: %s", exc)

    # Filtra URLs seguras e remove páginas de busca
    valid_results: list[dict] = []
    for c in found_candidates:
        u = c["url"]
        try:
            validate_url_host(u)
        except UnsafeURLError:
            continue
        if not _is_brazilian_store_url(u):
            continue
        parsed = urlparse(u)
        path = parsed.path.lower()
        if "/search" in path or "/busca" in path or "/pesquisa" in path or "google.com" in (parsed.hostname or ""):
            continue
        if target_store and target_store.lower() not in c["store"].lower():
            continue
        valid_results.append(c)

    return valid_results


def search_kabum_offers(keyword: str, limit: int = 3) -> list[dict]:
    """Busca ofertas na KaBuM! via API pública de catálogo v2 com fallback HTML."""
    if not keyword or not keyword.strip():
        return []
    if "PYTEST_CURRENT_TEST" in os.environ and getattr(httpx.get, "__module__", "") == "httpx":
        return []

    import urllib.parse

    encoded_kw = urllib.parse.quote_plus(keyword.strip())
    results: list[dict] = []

    # 1. API pública v2 de catálogo
    try:
        api_url = (
            f"https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products?"
            f"query={encoded_kw}&page_number=1&page_size={limit}&facet_filters=&sort=most_searched"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        r = _http_get(api_url, headers=headers, timeout=6.0)
        if r.status_code == 200:
            data = r.json()
            items = data.get("data", []) if isinstance(data, dict) else []
            for item in items:
                prod_id = item.get("id") or item.get("code")
                attrs = item.get("attributes", {})
                if attrs.get("available") is False or item.get("available") is False:
                    continue
                title = attrs.get("title") or item.get("title")
                if not prod_id or not title:
                    continue
                raw_price = attrs.get("price_with_discount") or attrs.get("price") or item.get("price")
                price = parse_brl(raw_price) if raw_price is not None else None
                if price is None or price <= 0:
                    continue
                url = f"https://www.kabum.com.br/produto/{prod_id}"
                results.append({
                    "store": "KaBuM!",
                    "title": title.strip(),
                    "url": url,
                    "price": price,
                    "source": "kabum:api",
                })
                if len(results) >= limit:
                    return results
    except Exception as exc:
        log.debug("Erro na API da KaBuM!: %s", exc)

    # 2. Fallback HTML se a API não retornou resultados
    if not results:
        try:
            from bs4 import BeautifulSoup

            search_url = f"https://www.kabum.com.br/busca/{encoded_kw}"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            r = _http_get(search_url, headers=headers, timeout=6.0, follow_redirects=True)
            if r.status_code == 200 and getattr(r, "text", None):
                soup = BeautifulSoup(r.text, "html.parser")
                cards = soup.select("article.productCard, .productCard, a[href*='/produto/']")
                seen_urls = set()
                for card in cards:
                    link_el = card if card.name == "a" else card.select_one("a[href*='/produto/']")
                    if not link_el or not link_el.get("href"):
                        continue
                    href = link_el["href"]
                    if not href.startswith("http"):
                        href = f"https://www.kabum.com.br{href}"
                    href = href.split("?")[0]
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    title_el = card.select_one(".nameCard, h2, span.nameCard")
                    title = title_el.get_text(strip=True) if title_el else ""
                    price_el = card.select_one(".priceCard, span.priceCard")
                    price = parse_brl(price_el.get_text(strip=True)) if price_el else None
                    if title and price is not None and price > 0:
                        results.append({
                            "store": "KaBuM!",
                            "title": title,
                            "url": href,
                            "price": price,
                            "source": "kabum:search",
                        })
                        if len(results) >= limit:
                            break
        except Exception as exc:
            log.debug("Erro no fallback HTML da KaBuM!: %s", exc)

    return results[:limit]


def search_amazon_offers(keyword: str, limit: int = 3) -> list[dict]:
    """Busca ofertas na Amazon Brasil via busca pública e parse dos resultados com ASIN."""
    if not keyword or not keyword.strip():
        return []
    if "PYTEST_CURRENT_TEST" in os.environ and getattr(httpx.get, "__module__", "") == "httpx":
        return []

    import urllib.parse
    from bs4 import BeautifulSoup

    results: list[dict] = []
    try:
        encoded_kw = urllib.parse.quote_plus(keyword.strip())
        url = f"https://www.amazon.com.br/s?k={encoded_kw}"
        r = _http_get(url, headers=AMAZON_HEADERS, timeout=6.0, follow_redirects=True)
        if r.status_code == 200 and getattr(r, "text", None):
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select('div[data-component-type="s-search-result"]')
            seen_asins = set()
            for card in cards:
                asin = card.get("data-asin", "").strip()
                if not asin or asin in seen_asins:
                    continue
                seen_asins.add(asin)

                title_el = card.select_one("h2 span") or card.select_one("h2")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                price = None
                offscreen = card.select_one(".a-price .a-offscreen")
                if offscreen:
                    price = parse_brl(offscreen.get_text(strip=True))
                if price is None:
                    whole = card.select_one(".a-price-whole")
                    fraction = card.select_one(".a-price-fraction")
                    if whole:
                        val = whole.get_text(strip=True)
                        if fraction:
                            val += "," + fraction.get_text(strip=True)
                        price = parse_brl(val)

                if price is None or price <= 0:
                    continue

                item_url = f"https://www.amazon.com.br/dp/{asin}"
                results.append({
                    "store": "Amazon",
                    "title": title,
                    "url": item_url,
                    "price": price,
                    "source": "amazon:search",
                })
                if len(results) >= limit:
                    break
    except Exception as exc:
        log.debug("Erro na busca da Amazon: %s", exc)

    return results[:limit]


def _gemini_filter_and_rank(
    candidates: list[dict],
    product_name: str,
    category: str = "Notebook",
    force_heuristic: bool = False,
) -> list[dict]:
    """Utiliza o Gemini para eliminar acessórios e classificar a relevância dos links."""
    api_key = get_gemini_api_key()
    if api_key and candidates and not force_heuristic:
        items_desc = []
        for i, c in enumerate(candidates):
            price_str = f" - R$ {c['price']:.2f}" if c.get("price") else ""
            items_desc.append(f"[{i}] Loja: {c['store']} | Título: {c['title']}{price_str}")

        prompt = (
            f"Você é um especialista em curadoria de ofertas e e-commerce.\n"
            f"Produto desejado pelo usuário: \"{product_name}\" (Categoria: {category})\n\n"
            "Analise os seguintes links e ofertas encontrados:\n"
            + "\n".join(items_desc)
            + "\n\n"
            "REGRAS RÍGIDAS DE FILTRAGEM:\n"
            "1. Descarte sumariamente qualquer acessório, capa, carregador, fonte, cabo, película, "
            "peça de reposição ou suporte, a menos que o produto desejado seja explicitamente esse acessório.\n"
            "2. Descarte modelos de produtos claramente diferentes ou marcas incompatíveis.\n"
            "3. Para cada oferta que corresponda ao produto desejado, retorne seu index, confidence ('alta' ou 'media') "
            "e reason (explicação curta em português).\n\n"
            "Responda EXCLUSIVAMENTE em formato JSON com este schema:\n"
            '{"matches": [{"index": 0, "confidence": "alta", "reason": "Mesmo modelo e especificações"}]}'
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

        try:
            resp = _call_gemini_generate_content(headers=headers, payload=payload, timeout=8.0)
            if resp is not None and resp.status_code == 200:
                body = resp.json()
                cand_first = (body.get("candidates") or [{}])[0]
                text = (cand_first.get("content", {}).get("parts") or [{}])[0].get("text", "")
                text = text.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                parsed_json = json.loads(text)
                matches = parsed_json.get("matches", [])
                ranked = []
                for m in matches:
                    idx = m.get("index")
                    if isinstance(idx, int) and 0 <= idx < len(candidates):
                        item = dict(candidates[idx])
                        item["confidence"] = m.get("confidence", "alta")
                        item["reason"] = m.get("reason", "Modelo correspondente")
                        ranked.append(item)
                if ranked:
                    return ranked
        except Exception as exc:
            log.warning("Falha na curadoria do Gemini (usando fallback heurístico): %s", exc)

    # Fallback heurístico inteligente
    acc_prefixes = (
        "fonte ", "carregador ", "capa ", "película ", "pelicula ",
        "teclado para ", "bateria para ", "cabo ", "case ", "adaptador ", "suporte para ",
    )
    p_lower = product_name.lower()
    p_tokens = set(re.findall(r"\w{3,}", p_lower))

    filtered: list[dict] = []
    for c in candidates:
        t_lower = (c.get("title") or "").lower()
        is_acc = False
        for pref in acc_prefixes:
            if pref in t_lower and pref not in p_lower:
                is_acc = True
                break
        if is_acc:
            continue

        t_tokens = set(re.findall(r"\w{3,}", t_lower))
        overlap = len(p_tokens & t_tokens)
        if p_tokens and len(p_tokens) >= 2 and overlap < 1:
            continue

        item = dict(c)
        item.setdefault("confidence", "media")
        item.setdefault("reason", "Encontrado via busca na loja")
        filtered.append(item)

    return filtered


def _diversify_by_store(results: list[dict], max_results: int) -> list[dict]:
    """Intercala os resultados entre diferentes lojas (round-robin) para garantir diversidade.

    Agrupa os itens por loja normalizada mantendo a ordem original de relevância dentro
    de cada loja. Na 1ª rodada, seleciona o 1º item de cada loja; na 2ª rodada, o 2º item
    de cada loja, e assim sucessivamente até preencher max_results.
    """
    if not results or max_results <= 0:
        return []

    stores_map: dict[str, list[dict]] = {}
    for r in results:
        store_key = (r.get("store") or "Loja").strip().lower()
        if store_key not in stores_map:
            stores_map[store_key] = []
        stores_map[store_key].append(r)

    diversified: list[dict] = []
    max_items_in_store = max(len(items) for items in stores_map.values())
    for round_idx in range(max_items_in_store):
        for items in stores_map.values():
            if round_idx < len(items):
                diversified.append(items[round_idx])
                if len(diversified) >= max_results:
                    return diversified

    return diversified


def _validate_and_enrich_candidates(
    candidates: list[dict],
    max_scrape_checks: int = 4,
) -> list[dict]:
    """Valida lojas brasileiras e enriquece/valida candidatos sem preço via scraping em tempo real."""
    valid: list[dict] = []
    scrape_checks = 0

    for c in candidates:
        url = c.get("url", "")
        if not _is_brazilian_store_url(url):
            continue

        raw_price = c.get("price")
        if raw_price is not None:
            try:
                if float(raw_price) > 0:
                    valid.append(c)
                    continue
            except (ValueError, TypeError):
                pass

        # Candidato sem preço válido (ex: vindo de Gemini Grounding)
        if scrape_checks < max_scrape_checks:
            scrape_checks += 1
            try:
                res = scrape(url)
                if res and res.price is not None and res.price > 0 and res.available is not False:
                    c_updated = dict(c)
                    c_updated["price"] = res.final_price or res.price
                    if res.name and c.get("source", "").startswith("gemini:"):
                        c_updated["title"] = res.name
                    valid.append(c_updated)
                else:
                    log.debug("Candidato descartado (sem preço ou indisponível): %s", url)
            except Exception as exc:
                log.debug("Erro ao verificar candidato %s: %s", url, exc)
        else:
            log.debug("Limite de scrape checks atingido. Candidato sem preço descartado: %s", url)

    return valid


def discover_product_links(
    product_name: str,
    category: str = "Notebook",
    target_store: str | None = None,
    exclude_urls: list[str] | None = None,
    max_results: int = 6,
) -> dict:
    """Descobre ofertas reais do produto integrando APIs de lojas, Google Search Grounding e Gemini."""
    exclude_set = {canonicalize_url(u) for u in (exclude_urls or []) if u}
    candidates: list[dict] = []

    clean_store = (target_store or "").strip().lower()

    # 1. Shopee Oficial
    if not clean_store or "shopee" in clean_store:
        try:
            shopee_offers = search_shopee_offers(product_name, limit=4)
            candidates.extend(shopee_offers)
        except Exception as exc:
            log.warning("Erro ao buscar Shopee: %s", exc)

    # 2. Mercado Livre Oficial
    if not clean_store or "mercado" in clean_store or "ml" in clean_store:
        try:
            ml_offers = search_mercadolivre_offers(product_name, limit=4)
            candidates.extend(ml_offers)
        except Exception as exc:
            log.warning("Erro ao buscar Mercado Livre: %s", exc)

    # 3. KaBuM!
    if not clean_store or "kabum" in clean_store:
        try:
            kabum_offers = search_kabum_offers(product_name, limit=3)
            candidates.extend(kabum_offers)
        except Exception as exc:
            log.warning("Erro ao buscar KaBuM!: %s", exc)

    # 4. Amazon
    if not clean_store or "amazon" in clean_store:
        try:
            amazon_offers = search_amazon_offers(product_name, limit=3)
            candidates.extend(amazon_offers)
        except Exception as exc:
            log.warning("Erro ao buscar Amazon: %s", exc)

    # 5. Google Search Grounding via Gemini (apenas se necessário para economizar cota)
    api_key = get_gemini_api_key()
    unique_stores = {c.get("store") for c in candidates if c.get("store")}
    should_call_grounding = False
    if api_key:
        if clean_store and len(candidates) < 2:
            should_call_grounding = True
        elif not clean_store and len(unique_stores) < 3:
            should_call_grounding = True

    grounding_called = False
    if should_call_grounding:
        try:
            grounded = _gemini_grounded_search(product_name, category, target_store)
            if grounded:
                candidates.extend(grounded)
            grounding_called = True
        except Exception as exc:
            log.warning("Erro ao buscar no Gemini Grounding: %s", exc)

    # 6. Deduplicação e exclusão
    deduped: list[dict] = []
    seen_keys: set[str] = set(exclude_set)
    for c in candidates:
        u = c.get("url")
        if not u:
            continue
        canon = canonicalize_url(u)
        if canon not in seen_keys:
            seen_keys.add(canon)
            deduped.append(c)

    # Validação e enriquecimento (lojas brasileiras + scrape de candidatos sem preço)
    deduped = _validate_and_enrich_candidates(deduped)

    # 7. Curadoria e Ranking com Gemini ou Heurística
    # Economia de cota: se grounding já foi chamado nesta execução, usa filtragem heurística direta
    use_heuristic = grounding_called or not api_key
    results = _gemini_filter_and_rank(deduped, product_name, category, force_heuristic=use_heuristic)

    return {
        "ai_used": bool(api_key),
        "gemini_configured": bool(api_key),
        "query": product_name,
        "results": _diversify_by_store(results, max_results),
    }


def heal_expired_listing(
    product_name: str, 
    store: str,
    dead_url: str,
    category: str = "Notebook",
    css_selector: str | None = None,
) -> tuple[str | None, PriceResult | None, str | None]:
    """Busca automaticamente um anúncio substituto ativo para o mesmo produto na mesma loja."""
    clean_target = store.strip()
    discovered = discover_product_links(
        product_name=product_name,
        category=category,
        target_store=clean_target,
        exclude_urls=[dead_url],
        max_results=5,
    )
    candidates = discovered.get("results", [])

    dead_canon = canonicalize_url(dead_url)
    for cand in candidates:
        cand_url = cand.get("url")
        if not cand_url or canonicalize_url(cand_url) == dead_canon:
            continue

        cand_store = (cand.get("store") or "").lower()
        if clean_target.lower() not in cand_store and cand_store not in clean_target.lower():
            continue

        try:
            res = scrape(cand_url, css_selector)
            if res and res.price is not None and res.available:
                return cand_url, res, cand.get("title")
        except Exception as exc:
            log.warning("Erro ao validar substituto de link %s: %s", cand_url, exc)

    return None, None, None
