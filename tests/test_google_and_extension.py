"""Testes para integração com Google OAuth, Extensão de Navegador e Coleta Rápida."""
import io
import os
import time
import zipfile
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import (
    COOKIE_NAME,
    GOOGLE_OAUTH_MARKER,
    create_session_token,
    hash_password,
    verify_google_id_token,
)
from app.collector import collect_single_listing
from app.main import app
from app.scrapers import PriceResult


@pytest.fixture()
def clean_db(tmp_path, monkeypatch):
    """Garante banco limpo para cada teste."""
    db_file = tmp_path / "test_google_ext.db"
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        monkeypatch.setattr(db, "DB_PATH", db_file)
        with db._INIT_LOCK:
            db._INITIALIZED_TARGETS.discard(f"sqlite://{db_file}")
    with db._SETTINGS_LOCK:
        db._SETTINGS_CACHE.clear()
    db.init_db()
    if db_url:
        conn = db.get_conn()
        with conn:
            conn.execute("DELETE FROM prices")
            conn.execute("DELETE FROM listings")
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM app_settings")
        conn.close()
    return db_file


@pytest.fixture()
def client(clean_db):
    with TestClient(app) as c:
        yield c


def test_verify_google_id_token_unit():
    """Valida o parser e verificações do tokeninfo do Google."""
    with patch("httpx.get") as mock_get:
        # 1. Falha de rede / status != 200
        mock_get.return_value = MagicMock(status_code=400)
        assert verify_google_id_token("bad_cred", "my_client_id") is None

        # 2. aud divergente
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "aud": "wrong_client_id",
                "iss": "accounts.google.com",
                "email_verified": "true",
                "email": "user@gmail.com",
            },
        )
        assert verify_google_id_token("cred", "my_client_id") is None

        # 3. iss inválido
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "aud": "my_client_id",
                "iss": "evil.com",
                "email_verified": "true",
                "email": "user@gmail.com",
            },
        )
        assert verify_google_id_token("cred", "my_client_id") is None

        # 4. email_verified não é true
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "aud": "my_client_id",
                "iss": "accounts.google.com",
                "email_verified": "false",
                "email": "user@gmail.com",
            },
        )
        assert verify_google_id_token("cred", "my_client_id") is None

        # 5. Sucesso
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "aud": "my_client_id",
                "iss": "https://accounts.google.com",
                "email_verified": "true",
                "email": "João.Silva@Gmail.Com",
                "name": "João Silva",
                "sub": "1234567890",
            },
        )
        res = verify_google_id_token("valid_cred", "my_client_id")
        assert res is not None
        assert res["email"] == "joão.silva@gmail.com"
        assert res["name"] == "João Silva"
        assert res["sub"] == "1234567890"


def test_auth_config_endpoint(client, monkeypatch):
    """Testa endpoint GET /api/auth/config."""
    # Sem nada configurado
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    assert resp.json() == {"google_client_id": ""}

    # Configurado via app_settings
    db.set_setting("google_client_id", "from-db-setting.apps.google.com")
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    assert resp.json()["google_client_id"] == "from-db-setting.apps.google.com"

    # Configurado via variável de ambiente (tem prioridade)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "from-env.apps.google.com")
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    assert resp.json()["google_client_id"] == "from-env.apps.google.com"


def test_google_auth_and_profile_flow(client, clean_db, monkeypatch):
    """Testa criação via Google, unificação com conta existente e perfil sem senha anterior."""
    CLIENT_ID = "client-xyz.apps.google.com"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)

    # 1. Criação de novo usuário Google (primeiro usuário do sistema adota produto órfão)
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO products (name, category, target_price) VALUES ('Notebook Órfão', 'Notebook', 3000)")

    with patch("app.main.verify_google_id_token") as mock_verify:
        mock_verify.return_value = {
            "email": "novo_google@test.com",
            "name": "Google User",
            "sub": "sub123",
        }
        resp = client.post("/api/auth/google", json={"credential": "sample_token_google_valid"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "novo_google@test.com"
        assert data["name"] == "Google User"
        assert data["has_password"] is False
        assert bool(data["token"]) is True
        token = data["token"]
        user_id = data["id"]

    # Verifica se herdou produto órfão
    conn = db.get_conn()
    p = conn.execute("SELECT user_id FROM products WHERE name = 'Notebook Órfão'").fetchone()
    assert p["user_id"] == user_id

    # 2. Autentica via Bearer token
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == user_id
    assert resp.json()["has_password"] is False

    # 3. Usuário Google define uma senha no perfil SEM precisar de current_password
    resp = client.patch(
        "/api/auth/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": "minhanovasenha123"},
    )
    assert resp.status_code == 200
    assert resp.json()["has_password"] is True

    # 4. Agora que tem senha, tentar alterar a senha sem current_password deve falhar
    resp = client.patch(
        "/api/auth/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": "outrasenhaforte456"},
    )
    assert resp.status_code == 400

    # Alterar fornecendo a senha atual funciona
    resp = client.patch(
        "/api/auth/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "minhanovasenha123", "new_password": "outrasenhaforte456"},
    )
    assert resp.status_code == 200

    # 5. Unificação automática quando e-mail já existe com senha comum
    resp = client.post(
        "/api/auth/register",
        json={"name": "Carlos Existente", "email": "carlos@test.com", "password": "senhaantiga123"},
    )
    assert resp.status_code == 201
    carlos_id = resp.json()["id"]
    assert resp.json()["has_password"] is True

    # Login Google com o mesmo e-mail une à conta existente mantendo has_password=True
    with patch("app.main.verify_google_id_token") as mock_verify:
        mock_verify.return_value = {
            "email": "carlos@test.com",
            "name": "Carlos Google",
            "sub": "sub999",
        }
        resp = client.post("/api/auth/google", json={"credential": "token_carlos_google"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == carlos_id
        assert data["has_password"] is True


def test_google_auth_failures(client, monkeypatch):
    """Testa casos de erro no login Google."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    db.set_setting("google_client_id", "")
    resp = client.post("/api/auth/google", json={"credential": "1234567890"})
    assert resp.status_code == 400
    assert "GOOGLE_CLIENT_ID ausente" in resp.json()["detail"]

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "configured-id")
    with patch("app.main.verify_google_id_token", return_value=None):
        resp = client.post("/api/auth/google", json={"credential": "1234567890"})
        assert resp.status_code == 401
        assert "inválido ou expirado" in resp.json()["detail"]


def test_extension_token_endpoint(client, clean_db):
    """Testa geração de token de longa duração da extensão."""
    # Cria usuário e loga
    resp = client.post(
        "/api/auth/register",
        json={"name": "Dev User", "email": "dev@test.com", "password": "password123"},
    )
    assert resp.status_code == 201
    cookie_token = resp.json()["token"]

    # Gera token de extensão usando sessão
    resp = client.post("/api/auth/extension-token")
    assert resp.status_code == 200
    ext_data = resp.json()
    assert bool(ext_data["token"]) is True
    assert ext_data["expires_in_days"] == 180

    # Valida que o token de extensão autentica via Bearer
    ext_token = ext_data["token"]
    resp = client.get("/api/products", headers={"Authorization": f"Bearer {ext_token}"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_extension_quick_add_and_backfill(client, clean_db):
    """Testa quick-add criando produto + link e sincronização de histórico."""
    # 1. Registra Usuário 1
    resp1 = client.post(
        "/api/auth/register",
        json={"name": "User 1", "email": "user1@test.com", "password": "password123"},
    )
    token1 = resp1.json()["token"]

    # Quick-add criando produto novo + link
    url = "https://www.amazon.com.br/dp/B08N5WRWNW?ref=test"
    with patch("app.main.collect_single_listing") as mock_collect:
        resp = client.post(
            "/api/extension/quick-add",
            headers={"Authorization": f"Bearer {token1}"},
            json={
                "name": "MacBook Air M1",
                "category": "Notebook",
                "target_price": 5000.0,
                "store": "Amazon",
                "url": url,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "product_id" in data
        assert "listing_id" in data
        assert data["backfilled"] is False
        mock_collect.assert_called_once_with(data["listing_id"])

    prod_id = data["product_id"]
    listing_id1 = data["listing_id"]

    # Simula gravação de histórico de preços para o listing do User 1
    conn = db.get_conn()
    with conn:
        conn.execute(
            "INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at) "
            "VALUES (?, ?, 1, NULL, ?, '2026-10-24 10:00:00')",
            (listing_id1, 4999.0, 4999.0),
        )

    # Tentativa de adicionar o mesmo link no mesmo produto dá 409
    resp = client.post(
        "/api/extension/quick-add",
        headers={"Authorization": f"Bearer {token1}"},
        json={
            "product_id": prod_id,
            "store": "Amazon",
            "url": "https://www.amazon.com.br/dp/B08N5WRWNW",  # mesma canonical_key
        },
    )
    assert resp.status_code == 409

    # 2. Registra Usuário 2 e monitora o mesmo link (deve receber backfill imediato!)
    client.post("/api/auth/logout")
    resp2 = client.post(
        "/api/auth/register",
        json={"name": "User 2", "email": "user2@test.com", "password": "password123"},
    )
    token2 = resp2.json()["token"]

    with patch("app.main.collect_single_listing") as mock_collect2:
        resp = client.post(
            "/api/extension/quick-add",
            headers={"Authorization": f"Bearer {token2}"},
            json={
                "name": "Meu MacBook",
                "category": "Notebook",
                "target_price": 4800.0,
                "store": "Amazon",
                "url": "https://www.amazon.com.br/dp/B08N5WRWNW?tag=afiliado123",
            },
        )
        assert resp.status_code == 201
        data2 = resp.json()
        assert data2["backfilled"] is True
        # Como houve backfill, NÃO precisa disparar collect imediato
        mock_collect2.assert_not_called()

    # Confirma que os preços foram copiados para o novo listing
    conn = db.get_conn()
    prices2 = conn.execute("SELECT * FROM prices WHERE listing_id = ?", (data2["listing_id"],)).fetchall()
    assert len(prices2) == 1
    assert prices2[0]["price"] == 4999.0


def test_collect_single_listing_execution(clean_db):
    """Testa a função collect_single_listing e sincronização com irmãos."""
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO users (id, name, email, password_hash, telegram_chat_id) VALUES (1, 'Admin', 'a@t.com', 'h', '12345')")
        conn.execute("INSERT INTO products (id, user_id, name, target_price) VALUES (1, 1, 'Notebook Teste', 3500.0)")
        conn.execute("INSERT INTO products (id, user_id, name, target_price) VALUES (2, 1, 'Notebook Irmão', 3600.0)")
        conn.execute(
            "INSERT INTO listings (id, product_id, store, url, canonical_key, active) "
            "VALUES (10, 1, 'Amazon', 'https://www.amazon.com.br/dp/B08N5WRWNW', 'amazon.com.br:b08n5wrwnw', 1)"
        )
        conn.execute(
            "INSERT INTO listings (id, product_id, store, url, canonical_key, active) "
            "VALUES (20, 2, 'Amazon', 'https://www.amazon.com.br/dp/B08N5WRWNW?ref=x', 'amazon.com.br:b08n5wrwnw', 1)"
        )

    # Mock do scrape retornando preço que atinge o target_price
    mock_result = PriceResult(
        price=3200.0,
        available=True,
        source="json-ld",
        coupon_code="OFF100",
        final_price=3100.0,
    )

    with patch("app.collector.scrape", return_value=mock_result) as mock_scrape, \
         patch("app.collector.notify_price_drop") as mock_notify:
        ok = collect_single_listing(10)
        assert ok is True
        mock_scrape.assert_called_once_with("https://www.amazon.com.br/dp/B08N5WRWNW", None)
        assert mock_notify.called

    # Confirma gravação no listing 10 e no listing 20 (irmão de canonical_key)
    conn = db.get_conn()
    p10 = conn.execute("SELECT * FROM prices WHERE listing_id = 10").fetchall()
    p20 = conn.execute("SELECT * FROM prices WHERE listing_id = 20").fetchall()
    assert len(p10) == 1
    assert p10[0]["final_price"] == 3100.0
    assert len(p20) == 1
    assert p20[0]["final_price"] == 3100.0


def test_cors_and_csp(client):
    """Testa cabeçalhos CORS para extensões e cabeçalhos CSP."""
    # 1. Preflight OPTIONS para rota de API com Origin de extensão
    resp = client.options(
        "/api/extension/quick-add",
        headers={"Origin": "chrome-extension://abcdefghijklmnop12345678"},
    )
    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abcdefghijklmnop12345678"
    assert "Authorization" in resp.headers.get("access-control-allow-headers", "")

    # 2. Resposta normal com Origin de extensão
    resp = client.get(
        "/api/health",
        headers={"Origin": "chrome-extension://abcdefghijklmnop12345678"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abcdefghijklmnop12345678"

    # 3. CSP contém domínios do Google Identity Services
    csp = resp.headers.get("content-security-policy", "")
    assert "https://accounts.google.com" in csp
    assert "https://oauth2.googleapis.com" in csp
    assert "frame-src https://accounts.google.com" in csp


def test_extension_download_zip(client):
    """Verifica que GET /api/extension/download gera o .zip com os arquivos corretos e URL injetada."""
    resp = client.get("/api/extension/download")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    assert 'attachment; filename="price-tracker-extension.zip"' in resp.headers.get("content-disposition", "")

    # Inspeciona o conteúdo do .zip retornado
    zip_bytes = io.BytesIO(resp.content)
    with zipfile.ZipFile(zip_bytes, "r") as zf:
        namelist = zf.namelist()
        assert "price-tracker-extension/manifest.json" in namelist
        assert "price-tracker-extension/popup.html" in namelist
        assert "price-tracker-extension/popup.css" in namelist
        assert "price-tracker-extension/popup.js" in namelist
        assert "price-tracker-extension/MANUAL.md" in namelist
        assert any(name.startswith("price-tracker-extension/icons/") for name in namelist)

        # popup.js deve conter a URL do servidor injetada (no TestClient padrão é http://testserver)
        popup_js = zf.read("price-tracker-extension/popup.js").decode("utf-8")
        assert "http://testserver" in popup_js
        assert "const DEFAULT_SERVER_URL = 'http://testserver';" in popup_js

    # Testa também com headers simulando um proxy reverso (ex: https://meuservidor.com.br)
    resp2 = client.get(
        "/api/extension/download",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "tracker.empresa.com"},
    )
    assert resp2.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp2.content), "r") as zf2:
        popup_js2 = zf2.read("price-tracker-extension/popup.js").decode("utf-8")
        assert "const DEFAULT_SERVER_URL = 'https://tracker.empresa.com';" in popup_js2

    # Verifica que rota é pública (mesmo se Basic Auth estiver ativo)
    with patch("app.main.APP_USERNAME", "admin"), patch("app.main.APP_PASSWORD", "secret"):
        resp_auth = client.get("/api/extension/download")
        assert resp_auth.status_code == 200

