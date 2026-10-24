"""Módulo de autenticação, sessões seguras e controle de usuários.

Implementa hashing de senhas com scrypt (padrão de alta segurança da biblioteca padrão),
assinatura de cookies de sessão HMAC-SHA256, e controle de permissões por usuário.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import httpx
from fastapi import HTTPException, Request, Response

from .db import ensure_db_initialized, get_conn, get_setting, set_setting

COOKIE_NAME = "pt_session"
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
SESSION_MAX_AGE = SESSION_TTL_DAYS * 86400
GOOGLE_OAUTH_MARKER = "google_oauth_only"


def hash_password(password: str) -> str:
    """Gera hash scrypt seguro com salt aleatório de 16 bytes."""
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("ascii"),
        n=16384,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Verifica se a senha em texto puro corresponde ao hash scrypt armazenado."""
    try:
        parts = stored.split("$")
        if len(parts) != 3 or parts[0] != "scrypt":
            return False
        _, salt, digest = parts
        calc_digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt.encode("ascii"),
            n=16384,
            r=8,
            p=1,
            dklen=32,
        ).hex()
        return secrets.compare_digest(calc_digest, digest)
    except Exception:
        return False


def _get_secret_key() -> bytes:
    """Obtém ou gera a chave secreta HMAC para assinatura de sessões."""
    env_key = os.environ.get("APP_SECRET_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")

    ensure_db_initialized()
    db_key = get_setting("session_secret_key")
    if db_key:
        return db_key.encode("utf-8")

    new_key = secrets.token_hex(32)
    set_setting("session_secret_key", new_key)
    return new_key.encode("utf-8")


def _b64decode_unpadded(s: str) -> bytes:
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def create_session_token(user_id: int, email: str, ttl_seconds: int = SESSION_MAX_AGE) -> str:
    """Cria token assinado contendo uid, email, iat e exp."""
    now = int(time.time())
    payload = {
        "uid": user_id,
        "email": email,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")

    secret_key = _get_secret_key()
    sig = hmac.new(secret_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def verify_session_token(token: str) -> dict | None:
    """Valida a assinatura HMAC e expiração do token de sessão. Retorna o payload ou None."""
    if not token or "." not in token:
        return None
    payload_b64, _, sig_b64 = token.partition(".")
    if not payload_b64 or not sig_b64:
        return None

    try:
        secret_key = _get_secret_key()
        sig = hmac.new(secret_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        expected_sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")

        if not secrets.compare_digest(sig_b64, expected_sig_b64):
            return None

        payload_bytes = _b64decode_unpadded(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            return None

        if payload.get("exp", 0) <= time.time():
            return None

        return payload
    except Exception:
        return None


def set_session_cookie(response: Response, token: str, request: Request | None = None) -> None:
    """Define o cookie de sessão HttpOnly Lax."""
    is_secure = request is not None and request.url.scheme == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Remove o cookie de sessão."""
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _extract_token(request: Request) -> str | None:
    """Extrai token do cabeçalho Authorization (Bearer ...) ou cookie pt_session."""
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        return auth_header.strip()[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def get_current_user(request: Request) -> dict:
    """Dependência FastAPI: exige usuário autenticado via Bearer token ou cookie de sessão."""
    token = _extract_token(request)
    if token:
        payload = verify_session_token(token)
        if payload and "uid" in payload:
            ensure_db_initialized()
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT id, name, email, password_hash, telegram_chat_id, created_at FROM users WHERE id = ?",
                    (payload["uid"],),
                ).fetchone()
                if row:
                    user_dict = dict(row)
                    pwd = user_dict.pop("password_hash", None)
                    user_dict["has_password"] = bool(pwd and pwd != GOOGLE_OAUTH_MARKER)
                    return user_dict
            finally:
                conn.close()

    raise HTTPException(status_code=401, detail="Autenticação necessária")


def get_optional_or_current_user(request: Request) -> dict | None:
    """Dependência FastAPI: autentica o usuário ativo, ou permite modo legado se a tabela estiver vazia."""
    token = _extract_token(request)
    if token:
        payload = verify_session_token(token)
        if payload and "uid" in payload:
            ensure_db_initialized()
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT id, name, email, password_hash, telegram_chat_id, created_at FROM users WHERE id = ?",
                    (payload["uid"],),
                ).fetchone()
                if row:
                    user_dict = dict(row)
                    pwd = user_dict.pop("password_hash", None)
                    user_dict["has_password"] = bool(pwd and pwd != GOOGLE_OAUTH_MARKER)
                    return user_dict
            finally:
                conn.close()

    ensure_db_initialized()
    conn = get_conn()
    try:
        has_any_user = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    finally:
        conn.close()

    if has_any_user:
        raise HTTPException(status_code=401, detail="Autenticação necessária")
    return None


def verify_google_id_token(credential: str, client_id: str) -> dict | None:
    """Valida o id_token do Google via tokeninfo endpoint."""
    if not credential or not client_id:
        return None
    try:
        resp = httpx.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": credential},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("aud") != client_id:
            return None
        if data.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            return None
        if str(data.get("email_verified", "")).lower() != "true":
            return None
        email = data.get("email")
        if not email:
            return None
        return {
            "email": email.strip().lower(),
            "name": (data.get("name") or email.split("@")[0]).strip(),
            "sub": data.get("sub", ""),
        }
    except Exception:
        return None
