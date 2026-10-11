from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.notifier import (
    notify_price_drop,
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
    monkeypatch.setattr("app.notifier.TELEGRAM_BOT_TOKEN", "tg_token")
    monkeypatch.setattr("app.notifier.TELEGRAM_CHAT_ID", "tg_chat")

    with patch("app.notifier.send_telegram", return_value=True) as mock_tg:
        results = notify_price_drop(
            product_name="Monitor Gamer",
            store="Amazon",
            url="https://amazon.com.br/item",
            current_price=899.00,
            target_price=950.00,
        )
        assert results.get("telegram") is True
        mock_tg.assert_called_once()
