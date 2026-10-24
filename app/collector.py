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

from .db import ensure_db_initialized, get_conn
from .notifier import notify_price_drop, should_notify
from .scrapers import scrape

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
                """SELECT l.*, p.name AS product_name, p.target_price, p.last_alert_at, p.last_alert_price
                   FROM listings l JOIN products p ON p.id = l.product_id
                   WHERE l.active = 1"""
            ).fetchall()
        ]
    finally:
        conn.close()

    COLLECTOR_STATE["total"] = len(rows)
    _notify_listeners({"event": "start", "total": len(rows), "current": 0})
    stats = {"ok": 0, "failed": 0}
    # Rastreia alertas já disparados nesta mesma execução para evitar duplicados
    alerted_in_run: dict[int, tuple[str, float]] = {}
    # Rate-limiting por domínio: evita esperar entre lojas diferentes
    last_request_by_domain: dict[str, float] = {}
    try:
        for i, row in enumerate(rows):
            COLLECTOR_STATE["current"] = i + 1
            domain = (urlparse(row["url"]).hostname or row["store"] or "").lower()
            prev_ts = last_request_by_domain.get(domain)
            if prev_ts is not None:
                wait_needed = _domain_delay(domain, delay) - (time.monotonic() - prev_ts)
                if wait_needed > 0:
                    time.sleep(wait_needed)
            last_request_by_domain[domain] = time.monotonic()

            try:
                result = scrape(row["url"], row["css_selector"])
            except Exception as exc:  # rede, bloqueio, timeout...
                log.warning("[%s] erro em %s: %s", row["store"], row["url"], exc)
                stats["failed"] += 1
                COLLECTOR_STATE["stats"] = stats
                _notify_listeners({
                    "event": "progress", "current": i + 1, "total": len(rows),
                    "store": row["store"], "success": False,
                })
                continue

            if result.price is None:
                log.warning("[%s] preço não encontrado (%s) em %s", row["store"], result.source, row["url"])
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
                        """INSERT INTO prices (listing_id, price, available, coupon_code, final_price)
                           VALUES (?, ?, ?, ?, ?)""",
                        (row["id"], result.price, int(result.available), result.coupon_code, effective_price),
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
