import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import COOKIE_NAME, hash_password, verify_password, create_session_token, verify_session_token
from app.main import app


@pytest.fixture()
def clean_db(tmp_path, monkeypatch):
    """Garante banco limpo para cada teste de autenticação."""
    db_file = tmp_path / "test_auth.db"
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


def test_auth_crypto_primitives():
    """Valida funções de hash de senha e tokens HMAC."""
    pwd = "MinhaSenhaForte!123"
    hashed = hash_password(pwd)
    assert hashed.startswith("scrypt$")
    assert verify_password(pwd, hashed) is True
    assert verify_password("SenhaErrada", hashed) is False
    assert verify_password(pwd, "invalid_hash") is False

    token = create_session_token(user_id=1, email="test@example.com")
    payload = verify_session_token(token)
    assert payload is not None
    assert payload["uid"] == 1
    assert payload["email"] == "test@example.com"
    assert "exp" in payload

    # Token adulterado deve falhar
    tampered = token[:-4] + "abcd"
    assert verify_session_token(tampered) is None

    # Token expirado
    expired_token = create_session_token(user_id=1, email="test@example.com", ttl_seconds=-10)
    assert verify_session_token(expired_token) is None


def test_register_and_orphan_adoption(client, clean_db):
    """Testa criação de conta e garantia de que o 1º usuário adota produtos existentes."""
    # 1. Cria produto órfão antes do primeiro usuário existir (ex: migrado ou legado)
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO products (name, category, target_price) VALUES ('Notebook Órfão', 'Notebook', 3500)")
    conn.close()

    # 2. Registra o 1º usuário
    res = client.post(
        "/api/auth/register",
        json={"name": "Alice Silva", "email": "alice@exemplo.com", "password": "password123"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Alice Silva"
    assert data["email"] == "alice@exemplo.com"
    user_id = data["id"]
    assert COOKIE_NAME in client.cookies

    # 3. Verifica se o produto órfão foi associado ao 1º usuário
    conn = db.get_conn()
    p_row = conn.execute("SELECT user_id FROM products WHERE name = 'Notebook Órfão'").fetchone()
    conn.close()
    assert p_row["user_id"] == user_id

    # 4. GET /api/auth/me deve retornar dados do usuário logado
    me_res = client.get("/api/auth/me")
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["id"] == user_id
    assert me_data["email"] == "alice@exemplo.com"
    assert "bot_configured" in me_data

    # 5. E-mail duplicado deve falhar com 409
    dup_res = client.post(
        "/api/auth/register",
        json={"name": "Outra Alice", "email": "ALICE@exemplo.com", "password": "password123"},
    )
    assert dup_res.status_code == 409


def test_validation_rules(client):
    """Testa validações de entrada no registro e configuração."""
    # Nome curto
    assert client.post("/api/auth/register", json={"name": "A", "email": "a@b.com", "password": "password"}).status_code == 422

    # Senha curta
    assert client.post("/api/auth/register", json={"name": "Valido", "email": "a@b.com", "password": "123"}).status_code == 422

    # E-mail inválido
    assert client.post("/api/auth/register", json={"name": "Valido", "email": "invalido", "password": "password"}).status_code == 422

    # Telegram Chat ID inválido (contém letras)
    assert client.post(
        "/api/auth/register",
        json={"name": "Valido", "email": "ok@teste.com", "password": "password", "telegram_chat_id": "abc123"},
    ).status_code == 422


def test_login_logout_flow(client):
    """Testa ciclo de login, logout e bloqueio não autenticado."""
    # Cadastra usuário
    client.post(
        "/api/auth/register",
        json={"name": "Bob", "email": "bob@exemplo.com", "password": "senhasecreta"},
    )

    # Logout
    logout_res = client.post("/api/auth/logout")
    assert logout_res.status_code == 200

    # TestClient com cookie limpo: tentar acessar rotas protegidas deve retornar 401
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/products").status_code == 401

    # Login com senha errada -> 401
    bad_login = client.post("/api/auth/login", json={"email": "bob@exemplo.com", "password": "errada"})
    assert bad_login.status_code == 401

    # Login com sucesso
    good_login = client.post("/api/auth/login", json={"email": "bob@exemplo.com", "password": "senhasecreta"})
    assert good_login.status_code == 200
    assert good_login.json()["email"] == "bob@exemplo.com"
    assert COOKIE_NAME in client.cookies

    # Acesso restabelecido
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/products").status_code == 200


def test_user_data_isolation(clean_db):
    """Testa isolamento completo e inviolabilidade entre múltiplos usuários."""
    with TestClient(app) as client_a, TestClient(app) as client_b:
        # Registra User A
        res_a = client_a.post(
            "/api/auth/register",
            json={"name": "Alice", "email": "alice@multi.com", "password": "password123"},
        )
        assert res_a.status_code == 201

        # Registra User B
        res_b = client_b.post(
            "/api/auth/register",
            json={"name": "Bob", "email": "bob@multi.com", "password": "password123"},
        )
        assert res_b.status_code == 201

        # User A cria um produto e listing
        prod_a = client_a.post("/api/products", json={"name": "Notebook Alice", "target_price": 5000}).json()["id"]
        list_a = client_a.post("/api/listings", json={"product_id": prod_a, "store": "Loja A", "url": "https://lojaa.com/p"}).json()["id"]

        # User B cria um produto e listing
        prod_b = client_b.post("/api/products", json={"name": "Notebook Bob", "target_price": 4000}).json()["id"]
        list_b = client_b.post("/api/listings", json={"product_id": prod_b, "store": "Loja B", "url": "https://lojab.com/p"}).json()["id"]

        # Insere preços no banco
        conn = db.get_conn()
        with conn:
            conn.execute("INSERT INTO prices (listing_id, price, available) VALUES (?, 4800, 1)", (list_a,))
            conn.execute("INSERT INTO prices (listing_id, price, available) VALUES (?, 3800, 1)", (list_b,))
        conn.close()

        # User A lista produtos -> vê APENAS o produto de Alice
        prods_a = client_a.get("/api/products").json()
        assert len(prods_a) == 1
        assert prods_a[0]["name"] == "Notebook Alice"

        # User B lista produtos -> vê APENAS o produto de Bob
        prods_b = client_b.get("/api/products").json()
        assert len(prods_b) == 1
        assert prods_b[0]["name"] == "Notebook Bob"

        # Histórico geral isolado
        hist_a = client_a.get("/api/history").json()
        assert len(hist_a) == 1
        assert hist_a[0]["product_id"] == prod_a

        hist_b = client_b.get("/api/history").json()
        assert len(hist_b) == 1
        assert hist_b[0]["product_id"] == prod_b

        # Tentativas de acesso indevido cruzado de Bob no produto de Alice:
        # 1. Obter análise do produto de Alice
        assert client_b.get(f"/api/products/{prod_a}/analysis").status_code == 404

        # 2. Obter histórico do produto de Alice
        assert client_b.get(f"/api/products/{prod_a}/history").status_code == 404

        # 3. Atualizar produto de Alice
        assert client_b.patch(f"/api/products/{prod_a}", json={"target_price": 1000}).status_code == 404

        # 4. Criar listing no produto de Alice
        assert client_b.post("/api/listings", json={"product_id": prod_a, "store": "Hacker", "url": "https://hacker.com"}).status_code == 404

        # 5. Deletar listing de Alice
        assert client_b.delete(f"/api/listings/{list_a}").status_code == 404

        # 6. Deletar produto de Alice
        assert client_b.delete(f"/api/products/{prod_a}").status_code == 404

        # Verifica se o produto de Alice permaneceu intacto
        prods_a_after = client_a.get("/api/products").json()
        assert len(prods_a_after) == 1
        assert prods_a_after[0]["target_price"] == 5000


def test_telegram_settings_and_test_dispatch(client, monkeypatch):
    """Testa atualização de Chat ID do Telegram e disparo de mensagem de teste."""
    # Registra usuário
    client.post(
        "/api/auth/register",
        json={"name": "Carlos", "email": "carlos@exemplo.com", "password": "password123"},
    )

    # Disparo de teste sem chat_id configurado deve dar 400
    res_no_chat = client.post("/api/auth/telegram/test")
    assert res_no_chat.status_code == 400
    assert "Configure e salve" in res_no_chat.json()["detail"]

    # PATCH com Chat ID inválido (caracteres alfanuméricos)
    assert client.patch("/api/auth/telegram", json={"telegram_chat_id": "bad_id"}).status_code == 422

    # PATCH com Chat ID válido (suporta grupos/canais com sinal negativo: -10012345678)
    patch_res = client.patch("/api/auth/telegram", json={"telegram_chat_id": "-1001987654321"})
    assert patch_res.status_code == 200
    assert patch_res.json()["telegram_chat_id"] == "-1001987654321"

    # Confirma via /api/auth/me
    me_res = client.get("/api/auth/me")
    assert me_res.json()["telegram_chat_id"] == "-1001987654321"

    # Teste de envio com TELEGRAM_BOT_TOKEN configurado e sucesso mockado da API
    monkeypatch.setattr("app.notifier.TELEGRAM_BOT_TOKEN", "123456:BOT_TOKEN_MOCK")

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = {"ok": True}

    with patch("httpx.post", return_value=mock_resp_ok) as mock_post:
        test_res = client.post("/api/auth/telegram/test")
        assert test_res.status_code == 200
        assert test_res.json()["status"] == "ok"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "sendMessage" in args[0]
        assert kwargs["json"]["chat_id"] == "-1001987654321"
        assert "Carlos" in kwargs["json"]["text"]

    # Teste de envio com falha na API do Telegram (ex: bot bloqueado pelo usuário)
    mock_resp_err = MagicMock()
    mock_resp_err.status_code = 403
    mock_resp_err.json.return_value = {"ok": False, "description": "Forbidden: bot was blocked by the user"}
    mock_resp_err.text = '{"ok": false, "description": "Forbidden: bot was blocked by the user"}'

    with patch("httpx.post", return_value=mock_resp_err):
        test_fail = client.post("/api/auth/telegram/test")
        assert test_fail.status_code == 400
        assert "bot was blocked by the user" in test_fail.json()["detail"]


def test_collector_routes_alert_to_product_owner(clean_db, monkeypatch):
    """Testa se o coletor envia alerta ao telegram_chat_id específico do dono do produto."""
    monkeypatch.setattr("app.notifier.TELEGRAM_BOT_TOKEN", "mock_bot_token")

    # Cria dois usuários com chat_ids distintos no Telegram
    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.post("/api/auth/register", json={
            "name": "User 1", "email": "u1@test.com", "password": "password123", "telegram_chat_id": "111111",
        })
        client_b.post("/api/auth/register", json={
            "name": "User 2", "email": "u2@test.com", "password": "password123", "telegram_chat_id": "222222",
        })

        p1 = client_a.post("/api/products", json={"name": "Prod 1", "target_price": 100}).json()["id"]
        client_a.post("/api/listings", json={"product_id": p1, "store": "Loja 1", "url": "https://loja1.com/p"})

    from app.collector import _collect_all
    from app.scrapers.base import PriceResult

    fake_result = PriceResult(price=80.0, available=True, source="test")
    with patch("app.collector.scrape", return_value=fake_result), \
         patch("app.collector.notify_price_drop") as mock_notify:
        _collect_all(delay=(0, 0))

        # Verifica se o alerta foi direcionado para o chat_id do User 1 (111111)
        assert mock_notify.call_count == 1
        _, kwargs = mock_notify.call_args
        assert kwargs["telegram_chat_id"] == "111111"
        assert kwargs["product_name"] == "Prod 1"


def test_update_profile_success_and_cookie_refresh(client):
    """Testa atualização de dados cadastrais (nome, email, chat_id) e renovação do cookie."""
    client.post("/api/auth/register", json={
        "name": "Nome Original", "email": "original@teste.com", "password": "password123", "telegram_chat_id": "12345",
    })

    # Atualiza nome, email e telegram_chat_id
    res = client.patch("/api/auth/profile", json={
        "name": "Nome Atualizado",
        "email": "atualizado@teste.com",
        "telegram_chat_id": "-100999888",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Nome Atualizado"
    assert data["email"] == "atualizado@teste.com"
    assert data["telegram_chat_id"] == "-100999888"

    # Confirma que o novo cookie continua autenticado e GET /api/auth/me reflete a alteração
    me = client.get("/api/auth/me").json()
    assert me["name"] == "Nome Atualizado"
    assert me["email"] == "atualizado@teste.com"
    assert me["telegram_chat_id"] == "-100999888"

    # Limpeza de telegram_chat_id enviando string vazia
    res_clear = client.patch("/api/auth/profile", json={"telegram_chat_id": ""})
    assert res_clear.status_code == 200
    assert res_clear.json()["telegram_chat_id"] is None
    assert client.get("/api/auth/me").json()["telegram_chat_id"] is None


def test_update_profile_conflict_and_validation(clean_db):
    """Testa conflito de e-mail duplicado e validações de nome/email/chat_id."""
    with TestClient(app) as client_a, TestClient(app) as client_b:
        client_a.post("/api/auth/register", json={"name": "Alice", "email": "alice@exemplo.com", "password": "password123"})
        client_b.post("/api/auth/register", json={"name": "Bob", "email": "bob@exemplo.com", "password": "password123"})

        # Bob tenta alterar o próprio email para o email de Alice -> 409
        dup_res = client_b.patch("/api/auth/profile", json={"email": "alice@exemplo.com"})
        assert dup_res.status_code == 409
        assert "já está em uso" in dup_res.json()["detail"]

        # Bob envia o mesmo email dele (sem alteração) -> 200
        same_res = client_b.patch("/api/auth/profile", json={"email": "BOB@exemplo.com"})
        assert same_res.status_code == 200

        # Nome com menos de 2 caracteres -> 422
        assert client_b.patch("/api/auth/profile", json={"name": "B"}).status_code == 422

        # Email em formato inválido -> 422
        assert client_b.patch("/api/auth/profile", json={"email": "invalido"}).status_code == 422

        # Telegram Chat ID inválido -> 422
        assert client_b.patch("/api/auth/profile", json={"telegram_chat_id": "abc_chat"}).status_code == 422


def test_update_profile_password_changes(client):
    """Testa troca de senha com validação de senha atual e novo login."""
    client.post("/api/auth/register", json={
        "name": "Daniel", "email": "daniel@exemplo.com", "password": "senhaantiga123",
    })

    # 1. Tentar alterar senha com current_password incorreta -> 400
    bad_curr = client.patch("/api/auth/profile", json={
        "current_password": "senhaerrada",
        "new_password": "novasenha123",
    })
    assert bad_curr.status_code == 400
    assert "Senha atual incorreta" in bad_curr.json()["detail"]

    # 2. Tentar alterar senha sem enviar current_password -> 400
    no_curr = client.patch("/api/auth/profile", json={
        "new_password": "novasenha123",
    })
    assert no_curr.status_code == 400
    assert "Senha atual incorreta" in no_curr.json()["detail"]

    # 3. Nova senha curta (< 6 caracteres) -> 422
    short_pwd = client.patch("/api/auth/profile", json={
        "current_password": "senhaantiga123",
        "new_password": "123",
    })
    assert short_pwd.status_code == 422

    # 4. Alteração bem sucedida
    good_change = client.patch("/api/auth/profile", json={
        "current_password": "senhaantiga123",
        "new_password": "novasenha123",
    })
    assert good_change.status_code == 200

    # 5. Tentativa de login com a senha antiga falha com 401
    assert client.post("/api/auth/login", json={"email": "daniel@exemplo.com", "password": "senhaantiga123"}).status_code == 401

    # 6. Login com a nova senha funciona com 200
    login_new = client.post("/api/auth/login", json={"email": "daniel@exemplo.com", "password": "novasenha123"})
    assert login_new.status_code == 200
    assert login_new.json()["email"] == "daniel@exemplo.com"


def test_bot_username_auto_discovery(monkeypatch):
    """Testa descoberta automática do @username via getMe e fallback para env."""
    from app import notifier

    # 1. Caso explícito via variável de ambiente (remove @ se informado)
    monkeypatch.setattr(notifier, "_CACHED_BOT_USERNAME", None)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@MeuBotCustomizado")
    assert notifier.get_bot_username() == "MeuBotCustomizado"

    # 2. Sem bot username no env, mas com bot token -> consulta getMe
    monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "123:TOKEN_MOCK")
    monkeypatch.setattr(notifier, "_CACHED_BOT_USERNAME", None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"username": "PrecoAlertaBot"}}

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        username = notifier.get_bot_username()
        assert username == "PrecoAlertaBot"
        assert notifier._CACHED_BOT_USERNAME == "PrecoAlertaBot"
        mock_get.assert_called_once()
        assert "getMe" in mock_get.call_args[0][0]

    # Chamada subsequente usa cache sem chamar rede
    with patch("httpx.get") as mock_get_again:
        assert notifier.get_bot_username() == "PrecoAlertaBot"
        mock_get_again.assert_not_called()

    # 3. Falha de rede ou resposta não-200 -> retorna "" sem quebrar
    monkeypatch.setattr(notifier, "_CACHED_BOT_USERNAME", None)
    mock_err = MagicMock()
    mock_err.status_code = 401
    mock_err.text = "Unauthorized"
    with patch("httpx.get", return_value=mock_err):
        assert notifier.get_bot_username() == ""


def test_spa_routes_redirect(client):
    """Testa redirecionamento de rotas de navegação direta para hash SPA."""
    # Rota conhecida de conta -> /#conta
    res_conta = client.get("/conta", follow_redirects=False)
    assert res_conta.status_code == 302
    assert res_conta.headers["location"] == "/#conta"

    # Outras rotas SPA conhecidas
    res_alertas = client.get("/alertas", follow_redirects=False)
    assert res_alertas.status_code == 302
    assert res_alertas.headers["location"] == "/#alertas"

    res_produtos = client.get("/produtos", follow_redirects=False)
    assert res_produtos.status_code == 302
    assert res_produtos.headers["location"] == "/#produtos"

    # Rota desconhecida -> redirect para raiz "/"
    res_unknown = client.get("/rota-inexistente", follow_redirects=False)
    assert res_unknown.status_code == 302
    assert res_unknown.headers["location"] == "/"

    # Arquivo estático real na raiz (ex: index.html) -> 200 FileResponse
    res_index = client.get("/index.html")
    assert res_index.status_code == 200
