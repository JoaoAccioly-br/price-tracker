import os
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from app import db
from app.analyzer import analyze_price_history, get_product_analysis
from app.main import app


def test_insufficient_data():
    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    records = [
        {"price": 100.0, "checked_at": now - timedelta(days=2)},
        {"price": 95.0, "checked_at": now},
    ]
    res = analyze_price_history(records, current_price=95.0, now=now)
    assert res.verdict == "insufficient_data"
    assert "Coletando" in res.label
    assert res.badge_class == "badge-collecting"


def test_real_deal_genuine_discount():
    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    # 40 dias de preços constantes em 100.00
    records = []
    for day in range(40, 1, -2):
        records.append({"price": 100.0, "checked_at": now - timedelta(days=day)})
    # Hoje cai para 85.00 (15% de desconto real)
    records.append({"price": 85.0, "checked_at": now})

    res = analyze_price_history(records, current_price=85.0, now=now)
    assert res.verdict in ("real_deal", "all_time_low")
    assert res.is_real_deal is True
    assert res.diff_vs_avg_30d_pct <= -10.0
    assert "Promoção Real" in res.label or "Menor Preço" in res.label


def test_fake_deal_recent_inflation_metade_do_dobro():
    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    records = []
    # Nos últimos 30 a 10 dias atrás: preço normal de R$ 100
    for day in range(35, 10, -2):
        records.append({"price": 100.0, "checked_at": now - timedelta(days=day)})

    # Há 4 dias: preço sobe artificialmente para R$ 130 (+30%)
    records.append({"price": 130.0, "checked_at": now - timedelta(days=4)})
    records.append({"price": 130.0, "checked_at": now - timedelta(days=2)})

    # Hoje: "Super Promoção!" caiu para R$ 105 (acima do valor original de 100)
    records.append({"price": 105.0, "checked_at": now})

    res = analyze_price_history(records, current_price=105.0, now=now)
    assert res.verdict == "fake_deal"
    assert res.is_fake_deal is True
    assert "Promoção Falsa" in res.label
    assert res.badge_class == "badge-fake-deal"


def test_fake_deal_price_above_average():
    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    records = []
    for day in range(30, 0, -2):
        records.append({"price": 100.0, "checked_at": now - timedelta(days=day)})

    # Preço atual de 112 (12% acima da média dos 30 dias)
    res = analyze_price_history(records, current_price=112.0, now=now)
    assert res.verdict == "fake_deal"
    assert res.is_fake_deal is True
    assert "Promoção Falsa" in res.label


def test_normal_market_price():
    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    records = []
    for day in range(30, 0, -2):
        # flutua entre 99 e 101
        p = 100.0 if day % 4 == 0 else 101.0
        records.append({"price": p, "checked_at": now - timedelta(days=day)})

    res = analyze_price_history(records, current_price=100.5, now=now)
    assert res.verdict == "normal"
    assert res.badge_class == "badge-normal"


def test_api_product_analysis_endpoint(tmp_path, monkeypatch):
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_analyzer.db")
    db.init_db()
    if db_url:
        conn = db.get_conn()
        with conn:
            conn.execute("DELETE FROM prices")
            conn.execute("DELETE FROM listings")
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM app_settings")
        conn.close()

    with TestClient(app) as client:
        # Cria produto
        r_create = client.post("/api/products", json={"name": "Produto Analise", "target_price": 200.0})
        assert r_create.status_code == 201
        pid = r_create.json()["id"]

        # Consulta análise
        r_an = client.get(f"/api/products/{pid}/analysis")
        assert r_an.status_code == 200
        data = r_an.json()
        assert "verdict" in data
        assert "label" in data
        assert "badge_class" in data
        assert data["verdict"] == "insufficient_data"

