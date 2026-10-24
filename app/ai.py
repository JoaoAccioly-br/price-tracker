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

from .scrapers import scrape
from .scrapers.base import PriceResult, UnsafeURLError, canonicalize_url, validate_url_host
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


def get_gemini_api_key() -> str:
    """Retorna a chave da API do Gemini configurada no ambiente."""
    return (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )


def get_gemini_model() -> str:
    """Retorna o modelo Gemini configurado (padrão: gemini-2.5-flash)."""
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"


def _detect_store_name(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        for domain, name in KNOWN_STORE_DOMAINS.items():
            if host == domain or host.endswith(f".{domain}"):
                return name
        return host.removeprefix("www.")
    except Exception:
        return "Loja"


def _gemini_grounded_search(
    product_name: str,
    category: str = "Notebook",
    target_store: str | None = None,
) -> list[dict]:
    """Usa o Google Search Grounding do Gemini para descobrir URLs reais do produto na web."""
    api_key = get_gemini_api_key()
    if not api_key:
        return []

    model = get_gemini_model()
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    store_filter_hint = (
        f"especificamente na loja {target_store}"
        if target_store
        else "em lojas brasileiras confiáveis (como Amazon Brasil, KaBuM, Magazine Luiza, Mercado Livre, Shopee)"
    )
    prompt = (
        f"Encontre links diretos de páginas de compra ativas do produto '{product_name}' "
        f"(Categoria: {category}) {store_filter_hint}. "
        "Retorne links exatos e completos das páginas do produto (não use links de páginas de busca geral)."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    found_candidates: list[dict] = []
    seen_urls: set[str] = set()

    try:
        resp = _http_post(api_url, headers=headers, json=payload, timeout=12.0)
        if resp.status_code != 200:
            log.warning("Gemini Grounded Search falhou (%d): %s", resp.status_code, resp.text[:200])
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
        parsed = urlparse(u)
        path = parsed.path.lower()
        if "/search" in path or "/busca" in path or "/pesquisa" in path or "google.com" in (parsed.hostname or ""):
            continue
        if target_store and target_store.lower() not in c["store"].lower():
            continue
        valid_results.append(c)

    return valid_results


def _gemini_filter_and_rank(
    candidates: list[dict],
    product_name: str,
    category: str = "Notebook",
) -> list[dict]:
    """Utiliza o Gemini para eliminar acessórios e classificar a relevância dos links."""
    api_key = get_gemini_api_key()
    if api_key and candidates:
        model = get_gemini_model()
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

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
            resp = _http_post(api_url, headers=headers, json=payload, timeout=12.0)
            if resp.status_code == 200:
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
            shopee_offers = search_shopee_offers(product_name, limit=6)
            candidates.extend(shopee_offers)
        except Exception as exc:
            log.warning("Erro ao buscar Shopee: %s", exc)

    # 2. Mercado Livre Oficial
    if not clean_store or "mercado" in clean_store or "ml" in clean_store:
        try:
            ml_offers = search_mercadolivre_offers(product_name, limit=6)
            candidates.extend(ml_offers)
        except Exception as exc:
            log.warning("Erro ao buscar Mercado Livre: %s", exc)

    # 3. Google Search Grounding via Gemini (para Amazon, KaBuM, Magalu, etc.)
    api_key = get_gemini_api_key()
    if api_key and (not clean_store or clean_store not in ("shopee", "mercado livre", "mercadolivre") or len(candidates) < 3):
        try:
            grounded = _gemini_grounded_search(product_name, category, target_store)
            candidates.extend(grounded)
        except Exception as exc:
            log.warning("Erro ao buscar no Gemini Grounding: %s", exc)

    # 4. Deduplicação e exclusão
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

    # 5. Curadoria e Ranking com Gemini ou Heurística
    results = _gemini_filter_and_rank(deduped, product_name, category)

    return {
        "ai_used": bool(api_key),
        "gemini_configured": bool(api_key),
        "query": product_name,
        "results": results[:max_results],
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
