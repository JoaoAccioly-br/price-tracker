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

_TG_CLIENT: httpx.Client | None = None
_CACHED_BOT_USERNAME: str | None = None


def _get_tg_client() -> httpx.Client:
    global _TG_CLIENT
    if _TG_CLIENT is None or _TG_CLIENT.is_closed:
        _TG_CLIENT = httpx.Client(timeout=10.0)
    return _TG_CLIENT


def get_bot_username() -> str:
    """Retorna o username do bot do Telegram (sem @), buscando no .env ou via API getMe."""
    global _CACHED_BOT_USERNAME

    env_username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if env_username:
        return env_username

    if _CACHED_BOT_USERNAME is not None:
        return _CACHED_BOT_USERNAME

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or TELEGRAM_BOT_TOKEN.strip()
    if not token:
        return ""

    api_url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        if getattr(httpx.get, "__module__", "") != "httpx":
            resp = httpx.get(api_url, timeout=5.0)
        else:
            resp = _get_tg_client().get(api_url, timeout=5.0)

        if resp.status_code == 200:
            data = resp.json()
            username = data.get("result", {}).get("username", "").strip().lstrip("@")
            if username:
                _CACHED_BOT_USERNAME = username
                return username
        else:
            safe_body = resp.text.replace(token, "***")[:200]
            log.warning("Falha ao obter username do Telegram via getMe (%d): %s", resp.status_code, safe_body)
    except Exception as exc:
        safe_err = str(exc).replace(token, "***")
        log.warning("Exceção ao consultar getMe no Telegram: %s", safe_err)

    return ""


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
    analysis: dict | None = None,
) -> bool:
    """Envia alerta para o Telegram via Bot HTTP API oficial."""
    token = (bot_token or TELEGRAM_BOT_TOKEN).strip()
    chat = (chat_id or TELEGRAM_CHAT_ID).strip()

    if not token or not chat:
        log.warning("Telegram não configurado (token ou chat_id ausente).")
        return False

    coupon_text = f" (cupom: <code>{html.escape(coupon_code)}</code>)" if coupon_code else ""

    analysis_text = ""
    if analysis:
        d = analysis.to_dict() if hasattr(analysis, "to_dict") else analysis
        label = d.get("label", "")
        desc = d.get("description", "")
        avg30 = d.get("avg_30d")
        avg60 = d.get("avg_60d")
        min60 = d.get("min_60d")

        stats = []
        if avg30:
            stats.append(f"Média 30d: R$ {avg30:.2f}")
        if avg60:
            stats.append(f"Média 60d: R$ {avg60:.2f}")
        if min60:
            stats.append(f"Mín 60d: R$ {min60:.2f}")
        stats_line = f"\n📈 <i>{' | '.join(stats)}</i>" if stats else ""
        analysis_text = (
            f"\n\n📊 <b>Análise de Promoção:</b>\n"
            f"{html.escape(str(label))}{stats_line}\n"
            f"<i>{html.escape(str(desc))}</i>"
        )

    text = (
        f"🎯 <b>Alerta de Preço Atingido!</b>\n\n"
        f"📦 <b>Produto:</b> {html.escape(product_name)}\n"
        f"🏪 <b>Loja:</b> {html.escape(store)}\n"
        f"💰 <b>Preço:</b> R$ {current_price:.2f}{coupon_text}\n"
        f"🎯 <b>Alvo:</b> R$ {target_price:.2f}"
        f"{analysis_text}\n\n"
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
        if getattr(httpx.post, "__module__", "") != "httpx":
            resp = httpx.post(api_url, json=payload, timeout=10.0)
        else:
            resp = _get_tg_client().post(api_url, json=payload, timeout=10.0)
        if resp.status_code == 200:
            log.info("Alerta enviado com sucesso para o Telegram (chat_id: %s)", chat)
            return True
        safe_body = resp.text.replace(token, "***")[:300]
        log.error("Erro na API do Telegram: %d - %s", resp.status_code, safe_body)
        return False
    except Exception as exc:
        safe_err = str(exc).replace(token, "***")
        log.error("Exceção ao enviar para o Telegram: %s", safe_err)
        return False


def send_telegram_test(chat_id: str, user_name: str = "Usuário") -> tuple[bool, str]:
    """Envia uma mensagem amigável de teste para validar a integração do Telegram com o usuário."""
    token = TELEGRAM_BOT_TOKEN.strip()
    target_chat = (chat_id or "").strip()

    if not token:
        return False, "Bot do Telegram não está configurado no servidor (TELEGRAM_BOT_TOKEN ausente)"
    if not target_chat:
        return False, "Chat ID do Telegram não informado"

    text = (
        "✅ <b>Price Tracker conectado com sucesso!</b>\n\n"
        f"Olá, {html.escape(user_name)}! Você receberá alertas de queda de preço diretamente neste chat."
    )
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        if getattr(httpx.post, "__module__", "") != "httpx":
            resp = httpx.post(api_url, json=payload, timeout=10.0)
        else:
            resp = _get_tg_client().post(api_url, json=payload, timeout=10.0)

        if resp.status_code == 200:
            log.info("Mensagem de teste enviada com sucesso para o Telegram (chat_id: %s)", target_chat)
            return True, "Mensagem de teste enviada com sucesso!"

        safe_body = resp.text.replace(token, "***")[:200]
        try:
            err_json = resp.json()
            err_desc = err_json.get("description", safe_body)
        except Exception:
            err_desc = safe_body
        return False, f"Erro do Telegram: {err_desc}"
    except Exception as exc:
        safe_err = str(exc).replace(token, "***")
        return False, f"Falha na conexão com Telegram: {safe_err}"


def notify_price_drop(
    product_name: str,
    store: str,
    url: str,
    current_price: float,
    target_price: float,
    coupon_code: str | None = None,
    telegram_chat_id: str | None = None,
    analysis: dict | None = None,
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
            analysis=analysis,
        )

    if not results:
        log.info(
            "Alerta gerado para '%s' (R$ %.2f), mas TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não estão configurados.",
            product_name,
            current_price,
        )

    return results
