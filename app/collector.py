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

from .db import get_conn, init_db
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


def _collect_all(delay: tuple[float, float]) -> dict:
    COLLECTOR_STATE["running"] = True
    COLLECTOR_STATE["current"] = 0
    COLLECTOR_STATE["total"] = 0
    COLLECTOR_STATE["stats"] = {"ok": 0, "failed": 0}

    init_db()
    conn = get_conn()
    rows = conn.execute(
        """SELECT l.*, p.name AS product_name, p.target_price
           FROM listings l JOIN products p ON p.id = l.product_id
           WHERE l.active = 1"""
    ).fetchall()

    COLLECTOR_STATE["total"] = len(rows)
    _notify_listeners({"event": "start", "total": len(rows), "current": 0})
    stats = {"ok": 0, "failed": 0}
    try:
        for i, row in enumerate(rows):
            COLLECTOR_STATE["current"] = i + 1
            if i:
                time.sleep(random.uniform(*delay))  # não martelar o site
            try:
                result = scrape(row["url"], row["css_selector"], bool(row["use_browser"]))
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
            with conn:
                conn.execute(
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
                # ponto de extensão: troque por Telegram / e-mail
                log.warning("ALERTA: %s a R$ %.2f na %s (alvo R$ %.2f)%s",
                            row["product_name"], effective_price, row["store"], target, coupon_info)
    finally:
        conn.close()
        COLLECTOR_STATE["running"] = False
        COLLECTOR_STATE["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())  # UTC, igual ao banco
        _notify_listeners({
            "event": "done", "total": len(rows), "stats": stats, "last_run": COLLECTOR_STATE["last_run"],
        })

    return stats
