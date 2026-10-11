"""Módulo de notificações e alertas de preço via Telegram Bot.

Utiliza a API oficial de bots do Telegram (HTTP REST via HTTPS / porta 443),
o que é 100% gratuito, instantâneo e não sofre com bloqueio de portas SMTP
(como ocorre no plano gratuito do Render).
"""
from datetime import datetime, timezone
import html
import logging
import os

import httpx

log = logging.getLogger("notifier")

# --- Configurações do Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def should_notify(
    target_price: float | None,
    current_price: float,
    last_alert_at: str | None,
    last_alert_price: float | None,
    cooldown_hours: float = 24.0,
) -> bool:
    """Decide se um alerta deve ser enviado com base no preço e no histórico.

    Regras:
    1. Se não houver preço alvo ou o preço atual for maior que o alvo -> False.
    2. Se nunca foi alertado antes -> True.
    3. Se o preço caiu ainda mais em relação ao último alerta -> True.
    4. Se o tempo desde o último alerta for maior ou igual ao cooldown (ex: 24h) -> True.
    5. Caso contrário -> False (evita spam contínuo a cada ciclo do coletor).
    """
    if target_price is None or current_price > target_price:
        return False

    # Primeiro disparo para este produto
    if not last_alert_at:
        return True

    # Se o preço caiu ainda mais (pelo menos 1 centavo abaixo do último aviso)
    if last_alert_price is not None and current_price < (last_alert_price - 0.01):
        return True

    # Se já passou o período de cooldown (padrão 24 horas)
    try:
        clean_ts = str(last_alert_at).replace("T", " ")[:19]
        last_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        diff_hours = (now_dt - last_dt).total_seconds() / 3600.0
        return diff_hours >= cooldown_hours
    except Exception as exc:
        log.warning("Falha ao analisar data do último alerta '%s': %s", last_alert_at, exc)
        return True


def send_telegram(
    product_name: str,
    store: str,
    url: str,
    current_price: float,
    target_price: float,
    coupon_code: str | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Envia alerta para o Telegram via Bot HTTP API oficial."""
    token = (bot_token or TELEGRAM_BOT_TOKEN).strip()
    chat = (chat_id or TELEGRAM_CHAT_ID).strip()

    if not token or not chat:
        log.warning("Telegram não configurado (token ou chat_id ausente).")
        return False

    coupon_text = f" (cupom: <code>{html.escape(coupon_code)}</code>)" if coupon_code else ""
    text = (
        f"🎯 <b>Alerta de Preço Atingido!</b>\n\n"
        f"📦 <b>Produto:</b> {html.escape(product_name)}\n"
        f"🏪 <b>Loja:</b> {html.escape(store)}\n"
        f"💰 <b>Preço:</b> R$ {current_price:.2f}{coupon_text}\n"
        f"🎯 <b>Alvo:</b> R$ {target_price:.2f}\n\n"
        f'👉 <a href="{html.escape(url, quote=True)}">Acessar oferta na {html.escape(store)}</a>'
    )

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = httpx.post(api_url, json=payload, timeout=10.0)
        if resp.status_code == 200:
            log.info("Alerta enviado com sucesso para o Telegram (chat_id: %s)", chat)
            return True
        log.error("Erro na API do Telegram: %d - %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        log.error("Exceção ao enviar para o Telegram: %s", exc)
        return False


def notify_price_drop(
    product_name: str,
    store: str,
    url: str,
    current_price: float,
    target_price: float,
    coupon_code: str | None = None,
    telegram_chat_id: str | None = None,
    **kwargs,
) -> dict[str, bool]:
    """Ponto central de despacho de notificações via Telegram."""
    results = {}

    chat_target = telegram_chat_id or TELEGRAM_CHAT_ID
    if TELEGRAM_BOT_TOKEN and chat_target:
        results["telegram"] = send_telegram(
            product_name=product_name,
            store=store,
            url=url,
            current_price=current_price,
            target_price=target_price,
            coupon_code=coupon_code,
            chat_id=chat_target,
        )

    if not results:
        log.info(
            "Alerta gerado para '%s' (R$ %.2f), mas TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não estão configurados.",
            product_name,
            current_price,
        )

    return results
