from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.notifier import (
    notify_price_drop,
    parse_recipients,
    send_email_smtp,
    send_telegram,
    should_notify,
)


def test_should_notify_conditions():
    # 1. Sem alvo ou preço acima da meta
    assert not should_notify(target_price=None, current_price=100.0, last_alert_at=None, last_alert_price=None)
    assert not should_notify(target_price=100.0, current_price=105.0, last_alert_at=None, last_alert_price=None)

    # 2. Primeiro disparo (nunca alertado)
    assert should_notify(target_price=100.0, current_price=95.0, last_alert_at=None, last_alert_price=None)

    # 3. Disparado recentemente, mas preço caiu ainda mais
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert should_notify(
        target_price=100.0,
        current_price=90.0,
        last_alert_at=recent_ts,
        last_alert_price=95.0,
    )

    # 4. Disparado recentemente e preço igual ou subiu levemente (mas ainda <= target): NÃO deve alertar (cooldown ativo)
    assert not should_notify(
        target_price=100.0,
        current_price=95.0,
        last_alert_at=recent_ts,
        last_alert_price=95.0,
        cooldown_hours=24.0,
    )

    # 5. Já passou o período de cooldown (ex: 26 horas atrás): DEVE alertar novamente como lembrete
    old_ts = (now - timedelta(hours=26)).strftime("%Y-%m-%d %H:%M:%S")
    assert should_notify(
        target_price=100.0,
        current_price=95.0,
        last_alert_at=old_ts,
        last_alert_price=95.0,
        cooldown_hours=24.0,
    )


def test_send_email_smtp():
    with patch("smtplib.SMTP") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance

        success = send_email_smtp(
            subject="Teste SMTP",
            plain_text="Texto",
            html_content="<p>Html</p>",
            to_email="cliente@exemplo.com",
            host="smtp.gmail.com",
            port=587,
            user="usuario@gmail.com",
            password="app_password",
            use_ssl=False,
        )
        assert success is True
        mock_instance.starttls.assert_called_once()
        mock_instance.login.assert_called_once_with("usuario@gmail.com", "app_password")
        mock_instance.send_message.assert_called_once()


def test_send_telegram():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        success = send_telegram(
            product_name="Notebook Nitro",
            store="Kabum",
            url="https://kabum.com.br/item",
            current_price=4299.99,
            target_price=4500.00,
            coupon_code="OFF100",
            bot_token="123456:ABC-DEF",
            chat_id="987654",
        )
        assert success is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "sendMessage" in args[0]
        assert kwargs["json"]["chat_id"] == "987654"
        assert "Notebook Nitro" in kwargs["json"]["text"]
        assert "OFF100" in kwargs["json"]["text"]


def test_notify_price_drop_dispatch(monkeypatch):
    monkeypatch.setattr("app.notifier.SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr("app.notifier.SMTP_USER", "usuario@gmail.com")
    monkeypatch.setattr("app.notifier.SMTP_PASS", "senha123")
    monkeypatch.setattr("app.notifier.ALERT_EMAIL_TO", "teste@exemplo.com")
    monkeypatch.setattr("app.notifier.TELEGRAM_BOT_TOKEN", "tg_token")
    monkeypatch.setattr("app.notifier.TELEGRAM_CHAT_ID", "tg_chat")

    with patch("app.notifier.send_email_smtp", return_value=True) as mock_smtp, \
         patch("app.notifier.send_telegram", return_value=True) as mock_tg:
        results = notify_price_drop(
            product_name="Monitor Gamer",
            store="Amazon",
            url="https://amazon.com.br/item",
            current_price=899.00,
            target_price=950.00,
        )
        assert results.get("email_smtp") is True
        assert results.get("telegram") is True
        mock_smtp.assert_called_once()
        mock_tg.assert_called_once()


def test_multiple_recipients():
    # 1. Testar parser
    assert parse_recipients("user1@a.com, user2@b.com") == ["user1@a.com", "user2@b.com"]
    assert parse_recipients("user1@a.com; user2@b.com") == ["user1@a.com", "user2@b.com"]
    assert parse_recipients(["u1@a.com", "u2@b.com"]) == ["u1@a.com", "u2@b.com"]

    # 2. SMTP com múltiplos e-mails
    with patch("smtplib.SMTP") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance

        success = send_email_smtp(
            subject="Multi SMTP",
            plain_text="Texto",
            html_content="<p>H</p>",
            to_email=["u1@a.com", "u2@b.com"],
            host="smtp.gmail.com",
            port=587,
            user="user@gmail.com",
            password="pass",
        )
        assert success is True
        # Verifica se to_addrs no send_message recebeu a lista
        _, kwargs = mock_instance.send_message.call_args
        assert kwargs["to_addrs"] == ["u1@a.com", "u2@b.com"]
