import pytest
from fastapi.testclient import TestClient

from app import db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import os

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    else:
        db.init_db()
        conn = db.get_conn()
        with conn:
            conn.execute("DELETE FROM prices")
            conn.execute("DELETE FROM listings")
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM app_settings")
        conn.close()
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_flow(client):
    pid = client.post("/api/products", json={"name": "Notebook X", "target_price": 4000}).json()["id"]
    lid = client.post("/api/listings", json={"product_id": pid, "store": "loja-a", "url": "https://a.test/x"}).json()["id"]
    client.post("/api/listings", json={"product_id": pid, "store": "loja-b", "url": "https://b.test/x"})

    # duplicado -> 409 ; produto inexistente -> 404
    assert client.post("/api/listings", json={"product_id": pid, "store": "loja-a", "url": "https://a.test/x"}).status_code == 409
    assert client.post("/api/listings", json={"product_id": 999, "store": "z", "url": "https://z.test"}).status_code == 404

    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO prices (listing_id, price, checked_at) VALUES (?, 4500, '2026-10-01 10:00:00')", (lid,))
        conn.execute("INSERT INTO prices (listing_id, price, checked_at) VALUES (?, 4300, '2026-10-02 10:00:00')", (lid,))
    conn.close()

    products = client.get("/api/products").json()
    assert products[0]["best_price"] == 4300
    hist = client.get(f"/api/products/{pid}/history").json()
    assert [h["price"] for h in hist] == [4500, 4300]

    assert client.get("/").status_code == 200  # frontend estático
    status_res = client.get("/api/collect/status")
    assert status_res.status_code == 200
    assert "running" in status_res.json()

    # PATCH parcial: apenas target_price
    patch_res = client.patch(f"/api/products/{pid}", json={"target_price": 3800})
    assert patch_res.status_code == 200
    p = client.get("/api/products").json()[0]
    assert p["target_price"] == 3800
    assert p["name"] == "Notebook X"
    assert p["category"] == "Notebook"

    # PATCH parcial: apenas category
    patch_res2 = client.patch(f"/api/products/{pid}", json={"category": "Monitor"})
    assert patch_res2.status_code == 200
    p2 = client.get("/api/products").json()[0]
    assert p2["category"] == "Monitor"
    assert p2["target_price"] == 3800

    # DELETE
    del_res = client.delete(f"/api/products/{pid}")
    assert del_res.status_code == 200
    assert len(client.get("/api/products").json()) == 0


def test_ssrf_blocked_on_listing_creation(client):
    pid = client.post("/api/products", json={"name": "Produto SSRF"}).json()["id"]
    for bad_url in (
        "http://127.0.0.1:8000/admin",
        "http://localhost/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/router",
        "http://[::1]:8080/internal",
    ):
        res = client.post("/api/listings", json={"product_id": pid, "store": "interna", "url": bad_url})
        assert res.status_code == 422


def test_auth_fail_closed_when_partial_credentials(client, monkeypatch):
    from app import main as main_mod

    # Se apenas a senha for configurada (e o usuário esquecido), deve bloquear (401)
    monkeypatch.setattr(main_mod, "APP_USERNAME", "")
    monkeypatch.setattr(main_mod, "APP_PASSWORD", "supersecret")
    assert client.get("/api/products").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_domain_matching_prevents_subdomain_spoofing():
    from app.scrapers import _matches_domain

    assert _matches_domain("www.mercadolivre.com.br", "mercadolivre.com.br") is True
    assert _matches_domain("mercadolivre.com.br", "mercadolivre.com.br") is True
    assert _matches_domain("mercadolivre.com.br.evil.com", "mercadolivre.com.br") is False
    assert _matches_domain("evilmercadolivre.com.br", "mercadolivre.com.br") is False
