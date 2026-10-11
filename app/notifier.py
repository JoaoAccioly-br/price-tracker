"""Módulo de notificações e alertas de preço.

Suporta:
1. E-mail via SMTP padrão (Gmail com Senha de App, Outlook, etc.)
2. Telegram Bot (API HTTP REST - opcional e gratuito)

As credenciais são lidas de variáveis de ambiente. Se nenhum canal estiver configurado,
o módulo registra em log de aviso e segue sem falhar.
"""
from datetime import datetime, timezone
import html
import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

log = logging.getLogger("notifier")

# --- Configurações de E-mail (SMTP / Gmail) ---
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587").strip() or "587")
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "").lower() in ("1", "true", "yes")

# Destinatário(s) padrão de e-mail (suporta múltiplos separados por vírgula)
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "").strip()

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


def _build_email_content(
    product_name: str,
    store: str,
    url: str,
    current_price: float,
    target_price: float,
    coupon_code: str | None = None,
) -> tuple[str, str, str]:
    """Retorna a tupla (subject, plain_text, html_content)."""
    coupon_str = f" (cupom {coupon_code})" if coupon_code else ""
    subject = f"🎯 [Alerta de Preço] {product_name} por R$ {current_price:.2f} na {store}!"

    plain_text = (
        f"Alerta de Preço - Price Tracker\n\n"
        f"O produto atingiu o valor desejado!\n"
        f"Produto: {product_name}\n"
        f"Loja: {store}\n"
        f"Preço Atual: R$ {current_price:.2f}{coupon_str}\n"
        f"Preço Alvo: R$ {target_price:.2f}\n\n"
        f"Link da oferta:\n{url}\n"
    )

    safe_name = html.escape(product_name)
    safe_store = html.escape(store)
    safe_url = html.escape(url, quote=True)
    coupon_badge = (
        f'<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;margin-left:8px;">Cupom: {html.escape(coupon_code)}</span>'
        if coupon_code
        else ""
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
    .card {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
    .header {{ background: #b91c1c; color: #ffffff; padding: 20px 24px; }}
    .header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
    .body {{ padding: 24px; }}
    .product-title {{ font-size: 16px; font-weight: 600; margin: 0 0 16px 0; color: #0f172a; }}
    .price-box {{ background: #fef2f2; border-left: 4px solid #b91c1c; padding: 12px 16px; border-radius: 0 8px 8px 0; margin-bottom: 20px; }}
    .current-price {{ font-size: 24px; font-weight: 700; color: #b91c1c; margin: 0; }}
    .target-price {{ font-size: 13px; color: #64748b; margin: 4px 0 0 0; }}
    .meta-row {{ font-size: 14px; margin-bottom: 8px; color: #475569; }}
    .btn-wrap {{ text-align: center; margin-top: 24px; }}
    .btn {{ display: inline-block; background: #b91c1c; color: #ffffff !important; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px; }}
    .footer {{ font-size: 12px; color: #94a3b8; text-align: center; padding: 16px; background: #f8fafc; border-top: 1px solid #e2e8f0; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>🎯 Alerta de Preço Atingido</h1>
    </div>
    <div class="body">
      <div class="product-title">{safe_name}</div>
      <div class="price-box">
        <div class="current-price">R$ {current_price:.2f} {coupon_badge}</div>
        <div class="target-price">Seu alvo configurado era R$ {target_price:.2f}</div>
      </div>
      <div class="meta-row"><strong>Loja:</strong> {safe_store}</div>
      <div class="btn-wrap">
        <a href="{safe_url}" class="btn" target="_blank" rel="noopener noreferrer">Acessar Oferta na {safe_store}</a>
      </div>
    </div>
    <div class="footer">
      Você está recebendo este aviso porque cadastrou um alvo no Price Tracker.
    </div>
  </div>
</body>
</html>"""

    return subject, plain_text, html_content


def parse_recipients(to: str | list[str] | None) -> list[str]:
    """Extrai uma lista limpa de destinatários a partir de string (separada por vírgula) ou lista."""
    if to is None:
        raw = ALERT_EMAIL_TO
    elif isinstance(to, list):
        return [e.strip() for e in to if isinstance(e, str) and e.strip()]
    else:
        raw = str(to)

    if not raw:
        return []
    return [e.strip() for e in raw.replace(";", ",").split(",") if e.strip()]


def send_email_smtp(
    subject: str,
    plain_text: str,
    html_content: str,
    to_email: str | list[str] | None = None,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    use_ssl: bool | None = None,
) -> bool:
    """Envia e-mail via SMTP padrão (Gmail, Outlook, servidores próprios) para um ou mais destinatários."""
    smtp_host = (host or SMTP_HOST).strip()
    smtp_port = port or SMTP_PORT
    smtp_user = (user or SMTP_USER).strip()
    smtp_pass = (password or SMTP_PASS).strip()
    recipients = parse_recipients(to_email) or ([smtp_user] if smtp_user else [])
    ssl_flag = SMTP_USE_SSL if use_ssl is None else use_ssl

    if not smtp_host or not smtp_user or not smtp_pass or not recipients:
        log.warning("SMTP incompleto: host, user, senha ou destinatário(s) ausentes.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(plain_text)
    msg.add_alternative(html_content, subtype="html")

    try:
        if ssl_flag or smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=12.0) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg, to_addrs=recipients)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=12.0) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg, to_addrs=recipients)
        log.info("E-mail enviado via SMTP para %s", recipients)
        return True
    except Exception as exc:
        log.error("Exceção ao enviar e-mail via SMTP: %s", exc)
        return False


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
    to_email: str | list[str] | None = None,
    telegram_chat_id: str | None = None,
) -> dict[str, bool]:
    """Ponto central de despacho de notificações.

    Dispara para os canais que estiverem configurados:
    - E-mail via SMTP (se SMTP_HOST e SMTP_USER estiverem definidos)
    - Telegram (se TELEGRAM_BOT_TOKEN estiver definido)
    """
    results = {}

    # 1. Envio por E-mail via SMTP (Gmail, etc.)
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        subject, plain_text, html_content = _build_email_content(
            product_name=product_name,
            store=store,
            url=url,
            current_price=current_price,
            target_price=target_price,
            coupon_code=coupon_code,
        )
        results["email_smtp"] = send_email_smtp(subject, plain_text, html_content, to_email=to_email)

    # 2. Envio por Telegram (se configurado)
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
            "Alerta de preço gerado para '%s' (R$ %.2f), mas nenhum canal de notificação está configurado.",
            product_name,
            current_price,
        )

    return results
