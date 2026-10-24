"""Percorre todos os links ativos, lê o preço e grava no histórico.

Rode a cada 6-12h via cron:
    0 */8 * * *  cd /caminho/notebook-tracker && python -m app.cli collect
"""
import asyncio
import json
import logging
import random
import threading
import time
from urllib.parse import urlparse

from .ai import heal_expired_listing
from .db import ensure_db_initialized, get_conn
from .notifier import notify_price_drop, should_notify
from .scrapers import PriceResult, canonicalize_url, scrape

log = logging.getLogger("collector")

# Filas de ouvintes SSE (navegadores conectados) -> loop asyncio dono da fila.
# A coleta roda numa thread do threadpool; asyncio.Queue NÃO é thread-safe,
# então os eventos são entregues via loop.call_soon_threadsafe.
_LISTENERS: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
_LISTENERS_LOCK = threading.Lock()

# Garante uma única coleta por vez (o check de "running" sozinho tem condição de corrida)
_COLLECT_LOCK = threading.Lock()

COLLECTOR_STATE = {
    "running": False,
    "last_run": None,
    "total": 0,
    "current": 0,
    "stats": {"ok": 0, "failed": 0},
}


def add_listener(queue: asyncio.Queue) -> None:
    """Registra uma fila SSE. Deve ser chamada de dentro do event loop."""
    with _LISTENERS_LOCK:
        _LISTENERS[queue] = asyncio.get_running_loop()


def remove_listener(queue: asyncio.Queue) -> None:
    with _LISTENERS_LOCK:
        _LISTENERS.pop(queue, None)


def _safe_put(queue: asyncio.Queue, event_data: dict) -> None:
    try:
        queue.put_nowait(event_data)
    except asyncio.QueueFull:
        pass  # cliente lento: descarta o evento em vez de acumular memória


def _notify_listeners(event_data: dict) -> None:
    """Dispara o evento para todas as conexões SSE ativas (seguro entre threads)."""
    with _LISTENERS_LOCK:
        items = list(_LISTENERS.items())
    for q, loop in items:
        try:
            loop.call_soon_threadsafe(_safe_put, q, event_data)
        except RuntimeError:  # loop já encerrado
            remove_listener(q)


def get_status() -> dict:
    return dict(COLLECTOR_STATE)


def collect_all(delay: tuple[float, float] = (3.0, 8.0)) -> dict:
    if not _COLLECT_LOCK.acquire(blocking=False):
        return COLLECTOR_STATE["stats"]
    try:
        return _collect_all(delay)
    finally:
        _COLLECT_LOCK.release()


def _domain_delay(domain: str, default_delay: tuple[float, float]) -> float:
    """Retorna um intervalo menor para APIs oficiais autenticadas e completo para scraping HTML."""
    if domain.endswith("mercadolivre.com.br") or domain.endswith("shopee.com.br"):
        return random.uniform(0.4, 1.2)
    return random.uniform(*default_delay)


def _should_auto_heal(result: PriceResult | None, exc: Exception | None) -> bool:
    """Determina se o link deve acionar Self-Healing com IA.

    Cirúrgico e imune a falsos positivos causados por bloqueios temporários (CAPTCHA/WAF),
    timeouts de rede ou falta de credenciais/chaves de API.
    """
    if exc is not None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code is None:
            status_code = getattr(exc, "status_code", None)
        return status_code in (404, 410)

    if result is None:
        return False

    src = (result.source or "").strip().lower()

    # Bloqueios temporários, erro de configuração ou limitação técnica
    blocked_sources = {
        "amazon:captcha",
        "amazon:price-hidden",
        "shopee:no-credentials",
        "shopee:invalid-url",
        "shopee:http-429",
        "ml:no-token",
        "ml:sem-token",
        "ml:id-invalido",
        "ml:http-401",
        "ml:http-403",
        "ml:http-429",
        "ml:blocked",
        "aliexpress:csr-js-required",
    }
    if src in blocked_sources:
        return False

    transient_prefixes = (
        "shopee:exception-",
        "shopee:http-5",
        "ml:http-5",
        "ml:exception-",
    )
    if any(src.startswith(prefix) for prefix in transient_prefixes):
        return False

    # Sinais reais de link expirado ou sem estoque
    if result.available is False or result.price is None:
        return True

    return False


def _collect_all(delay: tuple[float, float]) -> dict:
    COLLECTOR_STATE["running"] = True
    COLLECTOR_STATE["current"] = 0
    COLLECTOR_STATE["total"] = 0
    COLLECTOR_STATE["stats"] = {"ok": 0, "failed": 0}

    ensure_db_initialized()
    conn = get_conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """SELECT l.*, p.name AS product_name, p.category AS product_category, p.target_price,
                          p.last_alert_at, p.last_alert_price,
                          u.telegram_chat_id AS user_chat_id
                   FROM listings l
                   JOIN products p ON p.id = l.product_id
                   LEFT JOIN users u ON u.id = p.user_id
                   WHERE l.active = 1"""
            ).fetchall()
        ]
    finally:
        conn.close()

    COLLECTOR_STATE["total"] = len(rows)
    _notify_listeners({"event": "start", "total": len(rows), "current": 0})
    stats = {"ok": 0, "failed": 0}

    # Prepara canonical_key e determina a melhor URL para scraping por (canonical_key, css_selector)
    best_url_by_cache_key: dict[tuple[str, str], str] = {}
    for r in rows:
        ckey = (r.get("canonical_key") or "").strip()
        if not ckey:
            ckey = canonicalize_url(r["url"])
            r["canonical_key"] = ckey
        css = (r.get("css_selector") or "").strip()
        k = (ckey, css)
        url = r["url"]
        if k not in best_url_by_cache_key:
            best_url_by_cache_key[k] = url
        else:
            current_url = best_url_by_cache_key[k]
            if "pdp_npi=" in url and "pdp_npi=" not in current_url:
                best_url_by_cache_key[k] = url

    scraped_cache: dict[tuple[str, str], tuple[PriceResult | None, Exception | None, str]] = {}

    # Rastreia alertas já disparados nesta mesma execução para evitar duplicados
    alerted_in_run: dict[int, tuple[str, float]] = {}
    # Rate-limiting por domínio: evita esperar entre lojas diferentes
    last_request_by_domain: dict[str, float] = {}
    try:
        for i, row in enumerate(rows):
            COLLECTOR_STATE["current"] = i + 1
            ckey = row["canonical_key"]
            css = (row.get("css_selector") or "").strip()
            cache_key = (ckey, css)

            if cache_key not in scraped_cache:
                domain = (urlparse(row["url"]).hostname or row["store"] or "").lower()
                prev_ts = last_request_by_domain.get(domain)
                if prev_ts is not None:
                    wait_needed = _domain_delay(domain, delay) - (time.monotonic() - prev_ts)
                    if wait_needed > 0:
                        time.sleep(wait_needed)
                last_request_by_domain[domain] = time.monotonic()

                url_to_scrape = best_url_by_cache_key[cache_key]
                result: PriceResult | None = None
                exc: Exception | None = None
                checked_at_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                try:
                    result = scrape(url_to_scrape, row["css_selector"])
                except Exception as e:
                    exc = e
                scraped_cache[cache_key] = (result, exc, checked_at_utc)
            else:
                result, exc, checked_at_utc = scraped_cache[cache_key]

            # Auto-recuperação (Self-Healing): substitui anúncios expirados/esgotados/fora do ar
            needs_healing = _should_auto_heal(result, exc)
            if needs_healing:
                try:
                    new_url, healed_res, _ = heal_expired_listing(
                        product_name=row["product_name"],
                        store=row["store"],
                        dead_url=row["url"],
                        category=row.get("product_category") or "Notebook",
                        css_selector=row.get("css_selector"),
                    )
                    if new_url and healed_res and healed_res.price is not None:
                        new_ckey = canonicalize_url(new_url)
                        w_conn = get_conn()
                        try:
                            with w_conn:
                                w_conn.execute(
                                    "UPDATE listings SET url = ?, canonical_key = ? WHERE id = ?",
                                    (new_url, new_ckey, row["id"]),
                                )
                        finally:
                            w_conn.close()

                        row["url"] = new_url
                        row["canonical_key"] = new_ckey
                        result = healed_res
                        exc = None
                        log.info(
                            "✨ [Auto-Heal] Link expirado substituído na %s para '%s': %s",
                            row["store"],
                            row["product_name"],
                            new_url,
                        )
                except Exception as heal_err:
                    log.warning("Falha ao tentar auto-heal para %s: %s", row["url"], heal_err)

            if exc is not None:
                log.warning("[%s] erro em %s: %s", row["store"], row["url"], exc)
                stats["failed"] += 1
                COLLECTOR_STATE["stats"] = stats
                _notify_listeners({
                    "event": "progress", "current": i + 1, "total": len(rows),
                    "store": row["store"], "success": False,
                })
                continue

            if result is None or result.price is None:
                source = result.source if result else "unknown"
                log.warning("[%s] preço não encontrado (%s) em %s", row["store"], source, row["url"])
                stats["failed"] += 1
                COLLECTOR_STATE["stats"] = stats
                _notify_listeners({
                    "event": "progress", "current": i + 1, "total": len(rows),
                    "store": row["store"], "success": False,
                })
                continue

            effective_price = result.final_price or result.price
            w_conn = get_conn()
            try:
                with w_conn:
                    w_conn.execute(
                        """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (row["id"], result.price, int(result.available), result.coupon_code, effective_price, checked_at_utc),
                    )
                stats["ok"] += 1
                COLLECTOR_STATE["stats"] = stats
                coupon_info = f" [cupom {result.coupon_code} -> R$ {effective_price:.2f}]" if result.coupon_code else ""
                log.info("[%s] %s -> R$ %.2f%s (via %s)", row["store"], row["product_name"],
                         result.price, coupon_info, result.source)

                _notify_listeners({
                    "event": "progress", "current": i + 1, "total": len(rows),
                    "store": row["store"], "success": True, "price": result.price,
                    "final_price": effective_price, "coupon_code": result.coupon_code,
                })

                target = row["target_price"]
                if target and result.available and effective_price <= target:
                    log.warning("ALERTA: %s a R$ %.2f na %s (alvo R$ %.2f)%s",
                                row["product_name"], effective_price, row["store"], target, coupon_info)

                    prod_id = row["product_id"]
                    if prod_id in alerted_in_run:
                        curr_last_at, curr_last_price = alerted_in_run[prod_id]
                    else:
                        curr_last_at = row["last_alert_at"]
                        curr_last_price = row["last_alert_price"]

                    if should_notify(target, effective_price, curr_last_at, curr_last_price):
                        try:
                            from .analyzer import get_product_analysis
                            analysis = get_product_analysis(prod_id, current_price=effective_price, conn=w_conn)
                            notify_price_drop(
                                product_name=row["product_name"],
                                store=row["store"],
                                url=row["url"],
                                current_price=effective_price,
                                target_price=target,
                                coupon_code=result.coupon_code,
                                telegram_chat_id=(row.get("user_chat_id") or None),
                                analysis=analysis,
                            )
                            now_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                            with w_conn:
                                w_conn.execute(
                                    "UPDATE products SET last_alert_at = ?, last_alert_price = ? WHERE id = ?",
                                    (now_utc, effective_price, prod_id),
                                )
                            alerted_in_run[prod_id] = (now_utc, effective_price)
                        except Exception as err:
                            log.error("Erro ao processar notificação de alerta para o produto %s: %s", prod_id, err)
            finally:
                w_conn.close()
    finally:
        COLLECTOR_STATE["running"] = False
        COLLECTOR_STATE["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())  # UTC, igual ao banco
        _notify_listeners({
            "event": "done", "total": len(rows), "stats": stats, "last_run": COLLECTOR_STATE["last_run"],
        })

    return stats


def collect_single_listing(listing_id: int) -> bool:
    """Coleta imediatamente o preço de um único link (ex.: após quick-add ou cadastro) em background."""
    ensure_db_initialized()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT l.*, p.name AS product_name, p.target_price, p.last_alert_at, p.last_alert_price,
                      u.telegram_chat_id AS user_chat_id
               FROM listings l
               JOIN products p ON p.id = l.product_id
               LEFT JOIN users u ON u.id = p.user_id
               WHERE l.id = ? AND l.active = 1""",
            (listing_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return False

    row = dict(row)
    url = row["url"]
    css = row.get("css_selector")
    result: PriceResult | None = None
    try:
        result = scrape(url, css)
    except Exception as e:
        log.warning("[%s] erro em %s: %s", row.get("store"), url, e)
        return False

    if result is None or result.price is None:
        source = result.source if result else "unknown"
        log.warning("[%s] preço não encontrado (%s) em %s", row.get("store"), source, url)
        return False

    effective_price = result.final_price or result.price
    checked_at_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    w_conn = get_conn()
    try:
        with w_conn:
            w_conn.execute(
                """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (row["id"], result.price, int(result.available), result.coupon_code, effective_price, checked_at_utc),
            )

            ckey = (row.get("canonical_key") or "").strip() or canonicalize_url(url)
            if ckey:
                siblings = w_conn.execute(
                    "SELECT id FROM listings WHERE canonical_key = ? AND id != ? AND active = 1",
                    (ckey, row["id"]),
                ).fetchall()
                for s in siblings:
                    w_conn.execute(
                        """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (s["id"], result.price, int(result.available), result.coupon_code, effective_price, checked_at_utc),
                    )

        target = row.get("target_price")
        if target and result.available and effective_price <= target:
            if should_notify(target, effective_price, row.get("last_alert_at"), row.get("last_alert_price")):
                try:
                    from .analyzer import get_product_analysis
                    analysis = get_product_analysis(row["product_id"], current_price=effective_price, conn=w_conn)
                    notify_price_drop(
                        product_name=row["product_name"],
                        store=row["store"],
                        url=row["url"],
                        current_price=effective_price,
                        target_price=target,
                        coupon_code=result.coupon_code,
                        telegram_chat_id=(row.get("user_chat_id") or None),
                        analysis=analysis,
                    )
                    now_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                    with w_conn:
                        w_conn.execute(
                            "UPDATE products SET last_alert_at = ?, last_alert_price = ? WHERE id = ?",
                            (now_utc, effective_price, row["product_id"]),
                        )
                except Exception as err:
                    log.error("Erro ao processar notificação de alerta para o produto %s: %s", row["product_id"], err)
    finally:
        w_conn.close()

    _notify_listeners({
        "event": "done",
        "total": 1,
        "stats": {"ok": 1, "failed": 0},
        "last_run": checked_at_utc,
    })
    return True
