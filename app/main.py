"""API + frontend estático.

    uvicorn app.main:app --reload

Variáveis de ambiente opcionais:
    APP_USERNAME / APP_PASSWORD  -> ativa HTTP Basic Auth em todo o app
                                    (recomendado sempre que exposto na internet)
    ENABLE_DOCS=1                -> expõe /docs e /openapi.json (desligado por padrão)
"""
import asyncio
import base64
import binascii
from datetime import datetime, timedelta, timezone
import io
import json
import os
import re
import secrets
import sqlite3
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .analyzer import analyze_price_history, get_product_analysis
from .auth import (
    GOOGLE_OAUTH_MARKER,
    clear_session_cookie,
    create_session_token,
    get_current_user,
    get_optional_or_current_user,
    hash_password,
    set_session_cookie,
    verify_google_id_token,
    verify_password,
)
from .collector import add_listener, collect_all, collect_single_listing, get_status, remove_listener
from .db import DBConflictError, get_conn, get_setting, init_db
from . import notifier
from .notifier import get_bot_username, send_telegram_test
from .scrapers.base import UnsafeURLError, canonicalize_url, validate_url_host

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"

APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "").lower() in ("1", "true", "yes")

# Rotas liberadas sem autenticação (healthcheck do Docker e configs públicas)
PUBLIC_PATHS = {"/api/health", "/api/auth/config", "/api/extension/download"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Notebook Tracker",
    lifespan=lifespan,
    # Documentação interativa só quando explicitamente habilitada:
    # em produção ela expõe o mapa completo da API para qualquer visitante.
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# Compacta respostas JSON/HTML/CSS/JS > 1 KB (SSE é excluído automaticamente pelo Starlette)
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _is_authorized(header: str | None) -> bool:
    """Valida o header 'Authorization: Basic ...' em tempo constante e fail-closed."""
    user, password = "", ""
    valid_format = False
    if header and header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
            user, _, password = decoded.partition(":")
            valid_format = True
        except (binascii.Error, UnicodeDecodeError):
            valid_format = False

    # compare_digest evita ataques de timing; '&' (não 'and') para comparar sempre os dois
    user_ok = secrets.compare_digest(user, APP_USERNAME)
    pass_ok = secrets.compare_digest(password, APP_PASSWORD)
    both_configured = bool(APP_USERNAME and APP_PASSWORD)
    return bool(user_ok & pass_ok & valid_format & both_configured)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Trata preflight CORS para rotas /api/*
    origin = request.headers.get("origin") or request.headers.get("Origin") or ""
    if request.url.path.startswith("/api/") and request.method == "OPTIONS":
        allowed_origin = "*"
        if origin and (
            origin.startswith("chrome-extension://")
            or origin.startswith("moz-extension://")
            or origin.startswith("http://localhost")
            or origin.startswith("http://127.0.0.1")
        ):
            allowed_origin = origin
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": allowed_origin,
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
            },
        )

    # 1) Autenticação fail-closed: ativa se APP_USERNAME OU APP_PASSWORD estiverem definidos
    if (APP_USERNAME or APP_PASSWORD) and request.url.path not in PUBLIC_PATHS:
        if not _is_authorized(request.headers.get("authorization")):
            return PlainTextResponse(
                "Autenticação necessária", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="price-tracker", charset="UTF-8"'},
            )

    response = await call_next(request)

    # Suporte a CORS em respostas normais de /api/* para extensões
    if request.url.path.startswith("/api/") and origin:
        if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"

    # 2) Cabeçalhos de segurança (defesa em profundidade)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")  # impede clickjacking via iframe
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://accounts.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://accounts.google.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://accounts.google.com https://oauth2.googleapis.com; "
        "frame-src https://accounts.google.com; "
        "frame-ancestors 'none';",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ------------------------------------------------------------------- schemas
def _validate_http_url(value: str) -> str:
    value = value.strip()
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError("A URL deve começar com http:// ou https://")
    try:
        return validate_url_host(value)
    except UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc


class ProductIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    category: str | None = Field("Notebook", max_length=60)
    target_price: float | None = Field(None, ge=0, le=10_000_000)


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, max_length=60)
    target_price: float | None = Field(None, ge=0, le=10_000_000)


class ListingIn(BaseModel):
    product_id: int
    store: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=10, max_length=2048)
    css_selector: str | None = Field(None, max_length=300)

    _check_url = field_validator("url")(_validate_http_url)


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=150)
    password: str = Field(min_length=6, max_length=128)
    telegram_chat_id: str | None = Field(None, max_length=60)


class LoginIn(BaseModel):
    email: str
    password: str


class TelegramConfigIn(BaseModel):
    telegram_chat_id: str | None = Field(None, max_length=60)


class ProfileUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    email: str | None = Field(None, min_length=5, max_length=150)
    telegram_chat_id: str | None = Field(None, max_length=60)
    current_password: str | None = Field(None, max_length=128)
    new_password: str | None = Field(None, max_length=128)


class GoogleAuthIn(BaseModel):
    credential: str = Field(min_length=10)


class AIDiscoverIn(BaseModel):
    product_name: str | None = Field(None, max_length=200)
    product_id: int | None = None
    category: str = Field("Notebook", max_length=60)
    store: str | None = Field(None, max_length=60)


class QuickAddIn(BaseModel):
    product_id: int | None = None
    name: str | None = Field(None, max_length=200)
    category: str | None = Field("Notebook", max_length=60)
    target_price: float | None = Field(None, ge=0, le=10_000_000)
    store: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=10, max_length=2048)
    css_selector: str | None = Field(None, max_length=300)

    _check_url = field_validator("url")(_validate_http_url)


# ----------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    """Healthcheck leve (sem consultar o banco) usado pelo Docker."""
    return {"status": "ok"}


# ------------------------------------------------------------------- Auth API
@app.get("/api/auth/config")
def auth_config():
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip() or get_setting("google_client_id") or ""
    return {"google_client_id": client_id}


@app.post("/api/auth/google")
def auth_google(body: GoogleAuthIn, request: Request, response: Response):
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip() or get_setting("google_client_id") or ""
    if not client_id:
        raise HTTPException(400, "Login com Google não está configurado no servidor (GOOGLE_CLIENT_ID ausente)")
    info = verify_google_id_token(body.credential, client_id)
    if not info:
        raise HTTPException(401, "Token do Google inválido ou expirado")

    email = info["email"]
    name = info["name"]
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, name, email, password_hash, telegram_chat_id FROM users WHERE LOWER(email) = ?",
            (email,),
        ).fetchone()
        if row:
            user_id = row["id"]
            user_name = row["name"]
            chat_id = row["telegram_chat_id"]
            has_password = bool(row["password_hash"] and row["password_hash"] != GOOGLE_OAUTH_MARKER)
        else:
            is_first = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
            chat_id = (os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None) if is_first else None
            with conn:
                cur = conn.execute(
                    "INSERT INTO users (name, email, password_hash, telegram_chat_id) VALUES (?, ?, ?, ?)",
                    (name, email, GOOGLE_OAUTH_MARKER, chat_id),
                )
                user_id = cur.lastrowid
                if is_first:
                    conn.execute("UPDATE products SET user_id = ? WHERE user_id IS NULL", (user_id,))
            user_name = name
            has_password = False
    finally:
        conn.close()

    token = create_session_token(user_id=user_id, email=email)
    set_session_cookie(response, token, request)
    return {
        "id": user_id,
        "name": user_name,
        "email": email,
        "telegram_chat_id": chat_id,
        "has_password": has_password,
        "token": token,
    }


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response):
    email = body.email.strip().lower()
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "Nome deve ter no mínimo 2 caracteres")
    if "@" not in email or "." not in email:
        raise HTTPException(422, "E-mail inválido")
    if len(body.password) < 6:
        raise HTTPException(422, "Senha deve ter no mínimo 6 caracteres")

    chat_id = (body.telegram_chat_id or "").strip() or None
    if chat_id:
        if not re.match(r"^-?\d+$", chat_id):
            raise HTTPException(422, "Telegram Chat ID inválido (deve conter apenas dígitos ou sinal - inicial)")

    conn = get_conn()
    try:
        existing = conn.execute("SELECT 1 FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if existing:
            raise HTTPException(409, "E-mail já cadastrado")

        is_first = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
        if is_first and not chat_id:
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None

        pwd_hash = hash_password(body.password)
        with conn:
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, telegram_chat_id) VALUES (?, ?, ?, ?)",
                (name, email, pwd_hash, chat_id),
            )
            user_id = cur.lastrowid
            if is_first:
                conn.execute("UPDATE products SET user_id = ? WHERE user_id IS NULL", (user_id,))
    finally:
        conn.close()

    token = create_session_token(user_id=user_id, email=email)
    set_session_cookie(response, token, request)
    return {
        "id": user_id,
        "name": name,
        "email": email,
        "telegram_chat_id": chat_id,
        "has_password": True,
        "token": token,
        "bot_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or getattr(notifier, "TELEGRAM_BOT_TOKEN", "").strip()),
        "bot_username": get_bot_username(),
    }


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    email = body.email.strip().lower()
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT id, name, email, password_hash, telegram_chat_id FROM users WHERE LOWER(email) = ?",
            (email,),
        ).fetchone()
    finally:
        conn.close()

    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "E-mail ou senha inválidos")

    token = create_session_token(user_id=user["id"], email=user["email"])
    set_session_cookie(response, token, request)
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "telegram_chat_id": user["telegram_chat_id"],
        "has_password": True,
        "token": token,
        "bot_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or getattr(notifier, "TELEGRAM_BOT_TOKEN", "").strip()),
        "bot_username": get_bot_username(),
    }


@app.post("/api/auth/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(get_current_user)):
    return {
        **user,
        "bot_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or getattr(notifier, "TELEGRAM_BOT_TOKEN", "").strip()),
        "bot_username": get_bot_username(),
    }


@app.post("/api/auth/extension-token")
def generate_extension_token(user: dict = Depends(get_current_user)):
    ttl = 180 * 86400
    token = create_session_token(user_id=user["id"], email=user["email"], ttl_seconds=ttl)
    return {"token": token, "expires_in_days": 180}


@app.patch("/api/auth/profile")
def update_profile(
    body: ProfileUpdateIn,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, name, email, password_hash, telegram_chat_id FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Usuário não encontrado")

        if body.name is not None:
            new_name = body.name.strip()
            if len(new_name) < 2:
                raise HTTPException(422, "Nome deve ter no mínimo 2 caracteres")
        else:
            new_name = row["name"]

        if body.email is not None:
            new_email = body.email.strip().lower()
            if "@" not in new_email or "." not in new_email:
                raise HTTPException(422, "E-mail inválido")
            if new_email != row["email"].lower():
                conflict = conn.execute(
                    "SELECT 1 FROM users WHERE LOWER(email) = ? AND id != ?",
                    (new_email, user["id"]),
                ).fetchone()
                if conflict:
                    raise HTTPException(409, "Este e-mail já está em uso por outra conta")
        else:
            new_email = row["email"]

        if "telegram_chat_id" in body.model_fields_set:
            new_chat_id = (body.telegram_chat_id or "").strip() or None
            if new_chat_id is not None:
                if not re.match(r"^-?\d+$", new_chat_id):
                    raise HTTPException(422, "Telegram Chat ID inválido (deve conter apenas dígitos ou sinal - inicial)")
        else:
            new_chat_id = row["telegram_chat_id"]

        if body.new_password:
            is_google_user = (row["password_hash"] == GOOGLE_OAUTH_MARKER)
            if not is_google_user:
                if not body.current_password or not verify_password(body.current_password, row["password_hash"]):
                    raise HTTPException(400, "Senha atual incorreta")
            if len(body.new_password) < 6:
                raise HTTPException(422, "A nova senha deve ter no mínimo 6 caracteres")
            new_pwd_hash = hash_password(body.new_password)
        else:
            new_pwd_hash = row["password_hash"]

        with conn:
            conn.execute(
                "UPDATE users SET name = ?, email = ?, password_hash = ?, telegram_chat_id = ? WHERE id = ?",
                (new_name, new_email, new_pwd_hash, new_chat_id, user["id"]),
            )
    finally:
        conn.close()

    if new_email != row["email"]:
        token = create_session_token(user_id=user["id"], email=new_email)
        set_session_cookie(response, token, request)

    return {
        "id": user["id"],
        "name": new_name,
        "email": new_email,
        "telegram_chat_id": new_chat_id,
        "has_password": bool(new_pwd_hash and new_pwd_hash != GOOGLE_OAUTH_MARKER),
        "bot_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or getattr(notifier, "TELEGRAM_BOT_TOKEN", "").strip()),
        "bot_username": get_bot_username(),
    }


@app.patch("/api/auth/telegram")
def update_telegram(body: TelegramConfigIn, user: dict = Depends(get_current_user)):
    chat_id = (body.telegram_chat_id or "").strip() or None
    if chat_id:
        if not re.match(r"^-?\d+$", chat_id):
            raise HTTPException(422, "Telegram Chat ID inválido (deve conter apenas dígitos ou sinal - inicial)")

    conn = get_conn()
    try:
        with conn:
            conn.execute("UPDATE users SET telegram_chat_id = ? WHERE id = ?", (chat_id, user["id"]))
    finally:
        conn.close()

    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "telegram_chat_id": chat_id,
        "bot_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or getattr(notifier, "TELEGRAM_BOT_TOKEN", "").strip()),
        "bot_username": get_bot_username(),
    }


@app.post("/api/auth/telegram/test")
def test_telegram(user: dict = Depends(get_current_user)):
    chat_id = user.get("telegram_chat_id")
    if not chat_id:
        raise HTTPException(400, "Configure e salve seu Chat ID primeiro")

    ok, msg = send_telegram_test(chat_id, user.get("name", "Usuário"))
    if not ok:
        raise HTTPException(400, msg)
    return {"status": "ok", "message": msg}


@app.get("/api/products")
def list_products(user: dict | None = Depends(get_optional_or_current_user)):
    conn = get_conn()
    try:
        if user is not None:
            products = [
                dict(p)
                for p in conn.execute(
                    "SELECT * FROM products WHERE user_id = ? ORDER BY name",
                    (user["id"],),
                )
            ]
            rows = conn.execute(
                """WITH latest AS (
                       SELECT my_l.id AS listing_id, pr.price, pr.final_price, pr.coupon_code, pr.available, pr.checked_at,
                              ROW_NUMBER() OVER (PARTITION BY my_l.id
                                                 ORDER BY pr.checked_at DESC, pr.id DESC) AS rn
                       FROM listings my_l
                       JOIN listings sib ON sib.id = my_l.id OR (my_l.canonical_key IS NOT NULL AND my_l.canonical_key != '' AND sib.canonical_key = my_l.canonical_key)
                       JOIN prices pr ON pr.listing_id = sib.id
                   )
                   SELECT l.id, l.product_id, l.store, l.url,
                          lt.price AS last_price, lt.final_price, lt.coupon_code,
                          lt.available, lt.checked_at
                   FROM listings l
                   JOIN products p ON p.id = l.product_id
                   LEFT JOIN latest lt ON lt.listing_id = l.id AND lt.rn = 1
                   WHERE l.active = 1 AND p.user_id = ?
                   ORDER BY l.id""",
                (user["id"],),
            ).fetchall()

            cutoff_65d = (datetime.now(timezone.utc) - timedelta(days=65)).strftime("%Y-%m-%d %H:%M:%S")
            price_rows = conn.execute(
                """SELECT my_l.product_id, my_l.id AS listing_id, pr.price, pr.final_price, pr.checked_at
                   FROM listings my_l
                   JOIN listings sib ON sib.id = my_l.id OR (my_l.canonical_key IS NOT NULL AND my_l.canonical_key != '' AND sib.canonical_key = my_l.canonical_key)
                   JOIN prices pr ON pr.listing_id = sib.id
                   JOIN products p ON p.id = my_l.product_id
                   WHERE (pr.available = 1 OR pr.available IS NULL)
                     AND pr.checked_at >= ?
                     AND p.user_id = ?
                   ORDER BY pr.checked_at ASC, pr.id ASC""",
                (cutoff_65d, user["id"]),
            ).fetchall()
        else:
            products = [dict(p) for p in conn.execute("SELECT * FROM products ORDER BY name")]
            rows = conn.execute(
                """WITH latest AS (
                       SELECT my_l.id AS listing_id, pr.price, pr.final_price, pr.coupon_code, pr.available, pr.checked_at,
                              ROW_NUMBER() OVER (PARTITION BY my_l.id
                                                 ORDER BY pr.checked_at DESC, pr.id DESC) AS rn
                       FROM listings my_l
                       JOIN listings sib ON sib.id = my_l.id OR (my_l.canonical_key IS NOT NULL AND my_l.canonical_key != '' AND sib.canonical_key = my_l.canonical_key)
                       JOIN prices pr ON pr.listing_id = sib.id
                   )
                   SELECT l.id, l.product_id, l.store, l.url,
                          lt.price AS last_price, lt.final_price, lt.coupon_code,
                          lt.available, lt.checked_at
                   FROM listings l
                   LEFT JOIN latest lt ON lt.listing_id = l.id AND lt.rn = 1
                   WHERE l.active = 1
                   ORDER BY l.id"""
            ).fetchall()

            cutoff_65d = (datetime.now(timezone.utc) - timedelta(days=65)).strftime("%Y-%m-%d %H:%M:%S")
            price_rows = conn.execute(
                """SELECT my_l.product_id, my_l.id AS listing_id, pr.price, pr.final_price, pr.checked_at
                   FROM listings my_l
                   JOIN listings sib ON sib.id = my_l.id OR (my_l.canonical_key IS NOT NULL AND my_l.canonical_key != '' AND sib.canonical_key = my_l.canonical_key)
                   JOIN prices pr ON pr.listing_id = sib.id
                   WHERE (pr.available = 1 OR pr.available IS NULL)
                     AND pr.checked_at >= ?
                   ORDER BY pr.checked_at ASC, pr.id ASC""",
                (cutoff_65d,),
            ).fetchall()
    finally:
        conn.close()

    by_product: dict[int, list[dict]] = {}
    for r in rows:
        d = dict(r)
        by_product.setdefault(d.pop("product_id"), []).append(d)

    prices_by_product: dict[int, list[dict]] = {}
    seen_price_keys: set[tuple[int, str]] = set()
    for pr_r in price_rows:
        d = dict(pr_r)
        key = (d["listing_id"], d["checked_at"])
        if key not in seen_price_keys:
            seen_price_keys.add(key)
            prices_by_product.setdefault(d["product_id"], []).append(d)

    for p in products:
        listings = by_product.get(p["id"], [])
        in_stock_listings = [
            l for l in listings
            if l["last_price"] and (l.get("available") is None or l.get("available") == 1 or l.get("available") is True)
        ]
        best_l = None
        best_p = None
        if in_stock_listings:
            best_l = min(
                in_stock_listings,
                key=lambda x: (x["final_price"] if x.get("final_price") is not None else x["last_price"]),
            )
            best_p = best_l["final_price"] if best_l.get("final_price") is not None else best_l["last_price"]
        elif listings:
            priced = [l for l in listings if l.get("last_price")]
            if priced:
                best_l = min(
                    priced,
                    key=lambda x: (x["final_price"] if x.get("final_price") is not None else x["last_price"]),
                )
                best_p = best_l["final_price"] if best_l.get("final_price") is not None else best_l["last_price"]

        prod_records = prices_by_product.get(p["id"], [])
        analysis = analyze_price_history(prod_records, current_price=best_p)

        p.update({
            "listings": listings,
            "best_price": best_p,
            "best_store": best_l["store"] if best_l else None,
            "best_listing": best_l,
            "analysis": analysis.to_dict(),
        })
    return products


@app.get("/api/products/{product_id}/analysis")
def product_analysis(product_id: int, user: dict | None = Depends(get_optional_or_current_user)):
    """Retorna análise detalhada de 30 e 60 dias para detecção de promoção falsa."""
    conn = get_conn()
    try:
        if user is not None:
            p = conn.execute("SELECT id FROM products WHERE id = ? AND user_id = ?", (product_id, user["id"])).fetchone()
        else:
            p = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Produto não encontrado")
        return get_product_analysis(product_id, conn=conn).to_dict()
    finally:
        conn.close()


@app.get("/api/products/{product_id}/history")
def price_history(
    product_id: int,
    limit: int = Query(2000, ge=1, le=10000),
    user: dict | None = Depends(get_optional_or_current_user),
):
    conn = get_conn()
    try:
        if user is not None:
            p = conn.execute("SELECT id FROM products WHERE id = ? AND user_id = ?", (product_id, user["id"])).fetchone()
        else:
            p = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Produto não encontrado")

        rows = conn.execute(
            """SELECT my_l.id AS my_listing_id, my_l.store, pr.price, pr.final_price, pr.coupon_code, pr.available, pr.checked_at, pr.id
               FROM listings my_l
               JOIN listings sib ON sib.id = my_l.id OR (my_l.canonical_key IS NOT NULL AND my_l.canonical_key != '' AND sib.canonical_key = my_l.canonical_key)
               JOIN prices pr ON pr.listing_id = sib.id
               WHERE my_l.product_id = ?
               ORDER BY pr.checked_at DESC, pr.id DESC""",
            (product_id,),
        ).fetchall()

        seen = set()
        deduped = []
        for r in rows:
            key = (r["my_listing_id"], r["checked_at"])
            if key not in seen:
                seen.add(key)
                deduped.append({
                    "id": r["id"],
                    "store": r["store"],
                    "price": r["price"],
                    "final_price": r["final_price"],
                    "coupon_code": r["coupon_code"],
                    "available": r["available"],
                    "checked_at": r["checked_at"],
                })
                if len(deduped) >= limit:
                    break

        deduped.sort(key=lambda x: (x["checked_at"], x["id"]))
        return [{k: v for k, v in item.items() if k != "id"} for item in deduped]
    finally:
        conn.close()


@app.get("/api/history")
def full_history(
    limit: int = Query(50, ge=1, le=1000),
    user: dict | None = Depends(get_optional_or_current_user),
):
    """Retorna as últimas leituras de preços em todas as lojas para a tela de Histórico."""
    conn = get_conn()
    try:
        if user is not None:
            rows = conn.execute(
                """SELECT pr.id, p.id AS product_id, p.name AS product_name, p.target_price,
                          l.store, l.url, pr.price, pr.final_price, pr.coupon_code,
                          pr.available, pr.checked_at
                   FROM prices pr
                   JOIN listings l ON l.id = pr.listing_id
                   JOIN products p ON p.id = l.product_id
                   WHERE p.user_id = ?
                   ORDER BY pr.checked_at DESC, pr.id DESC
                   LIMIT ?""",
                (user["id"], limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT pr.id, p.id AS product_id, p.name AS product_name, p.target_price,
                          l.store, l.url, pr.price, pr.final_price, pr.coupon_code,
                          pr.available, pr.checked_at
                   FROM prices pr
                   JOIN listings l ON l.id = pr.listing_id
                   JOIN products p ON p.id = l.product_id
                   ORDER BY pr.checked_at DESC, pr.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.post("/api/products", status_code=201)
def create_product(body: ProductIn, user: dict | None = Depends(get_optional_or_current_user)):
    if not body.name or not body.name.strip():
        raise HTTPException(422, "Nome do produto é obrigatório")
    cat = (body.category or "Notebook").strip() or "Notebook"
    conn = get_conn()
    try:
        with conn:
            if user is not None:
                cur = conn.execute(
                    "INSERT INTO products (name, category, target_price, user_id) VALUES (?, ?, ?, ?)",
                    (body.name.strip(), cat, body.target_price, user["id"]),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO products (name, category, target_price) VALUES (?, ?, ?)",
                    (body.name.strip(), cat, body.target_price),
                )
    finally:
        conn.close()
    return {"id": cur.lastrowid}


@app.patch("/api/products/{product_id}")
def update_product(
    product_id: int,
    body: ProductUpdate,
    user: dict | None = Depends(get_optional_or_current_user),
):
    """Atualiza seletivamente o nome, categoria ou preço alvo de um produto."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        return {"status": "sem alterações", "id": product_id}

    set_clauses = []
    params = []
    if "name" in update_data:
        if not update_data["name"]:
            raise HTTPException(422, "Nome do produto é obrigatório")
        set_clauses.append("name = ?")
        params.append(update_data["name"])
    if "category" in update_data:
        set_clauses.append("category = ?")
        params.append(update_data["category"] or "Notebook")
    if "target_price" in update_data:
        set_clauses.append("target_price = ?")
        params.append(update_data["target_price"])

    params.append(product_id)
    if user is not None:
        where_clause = "WHERE id = ? AND user_id = ?"
        params.append(user["id"])
    else:
        where_clause = "WHERE id = ?"

    # set_clauses só contém literais fixos acima -> sem risco de SQL injection
    query = f"UPDATE products SET {', '.join(set_clauses)} {where_clause}"
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute(query, params)
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Produto não encontrado")
    return {"status": "atualizado", "id": product_id}


def _preserve_shared_history_before_delete(conn, listing_ids: list[int]) -> None:
    """Antes de excluir listings, preserva histórico em outros listings de mesma canonical_key."""
    if not listing_ids:
        return

    placeholders = ",".join("?" for _ in listing_ids)
    for lid in listing_ids:
        row = conn.execute("SELECT canonical_key FROM listings WHERE id = ?", (lid,)).fetchone()
        if not row:
            continue
        ckey = row["canonical_key"]
        if not ckey:
            continue

        survivors = conn.execute(
            f"SELECT id FROM listings WHERE canonical_key = ? AND id NOT IN ({placeholders})",
            (ckey, *listing_ids),
        ).fetchall()
        if not survivors:
            continue

        prices_to_copy = conn.execute(
            "SELECT price, available, coupon_code, final_price, checked_at FROM prices WHERE listing_id = ?",
            (lid,),
        ).fetchall()
        if not prices_to_copy:
            continue

        for s in survivors:
            s_id = s["id"]
            existing_ts_rows = conn.execute(
                "SELECT checked_at FROM prices WHERE listing_id = ?",
                (s_id,),
            ).fetchall()
            existing_ts = {r["checked_at"] for r in existing_ts_rows}
            for p in prices_to_copy:
                ts = p["checked_at"]
                if ts not in existing_ts:
                    conn.execute(
                        """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (s_id, p["price"], p["available"], p["coupon_code"], p["final_price"], ts),
                    )
                    existing_ts.add(ts)


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, user: dict | None = Depends(get_optional_or_current_user)):
    """Remove um produto e seus listings/histórico associados (ON DELETE CASCADE)."""
    conn = get_conn()
    try:
        with conn:
            if user is not None:
                p = conn.execute("SELECT id FROM products WHERE id = ? AND user_id = ?", (product_id, user["id"])).fetchone()
            else:
                p = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
            if not p:
                raise HTTPException(404, "Produto não encontrado")

            l_rows = conn.execute("SELECT id FROM listings WHERE product_id = ?", (product_id,)).fetchall()
            listing_ids = [r["id"] for r in l_rows]

            _preserve_shared_history_before_delete(conn, listing_ids)

            if user is not None:
                cur = conn.execute("DELETE FROM products WHERE id = ? AND user_id = ?", (product_id, user["id"]))
            else:
                cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Produto não encontrado")
    return {"status": "removido", "id": product_id}


@app.post("/api/extension/quick-add", status_code=201)
def extension_quick_add(
    body: QuickAddIn,
    background: BackgroundTasks,
    user: dict | None = Depends(get_optional_or_current_user),
):
    conn = get_conn()
    try:
        if body.product_id is not None:
            if user is not None:
                p = conn.execute("SELECT id FROM products WHERE id = ? AND user_id = ?", (body.product_id, user["id"])).fetchone()
            else:
                p = conn.execute("SELECT id FROM products WHERE id = ?", (body.product_id,)).fetchone()
            if not p:
                raise HTTPException(404, "Produto não encontrado")
            pid = body.product_id
        else:
            if not body.name or not body.name.strip():
                raise HTTPException(422, "Nome do produto é obrigatório")
            cat = (body.category or "Notebook").strip() or "Notebook"
            with conn:
                if user is not None:
                    cur = conn.execute(
                        "INSERT INTO products (name, category, target_price, user_id) VALUES (?, ?, ?, ?)",
                        (body.name.strip(), cat, body.target_price, user["id"]),
                    )
                else:
                    cur = conn.execute(
                        "INSERT INTO products (name, category, target_price) VALUES (?, ?, ?)",
                        (body.name.strip(), cat, body.target_price),
                    )
                pid = cur.lastrowid

        ckey = canonicalize_url(body.url)
        existing = conn.execute(
            """SELECT 1 FROM listings
               WHERE product_id = ?
                 AND (url = ? OR (canonical_key IS NOT NULL AND canonical_key != '' AND canonical_key = ?))""",
            (pid, body.url, ckey),
        ).fetchone()
        if existing:
            raise HTTPException(409, "Esse link já está cadastrado para o produto")

        backfilled = False
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO listings (product_id, store, url, css_selector, canonical_key) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pid, body.store.strip(), body.url, body.css_selector, ckey),
                )
                new_listing_id = cur.lastrowid

                if ckey:
                    hist_rows = conn.execute(
                        """SELECT pr.price, pr.available, pr.coupon_code, pr.final_price, pr.checked_at
                           FROM prices pr
                           JOIN listings l ON l.id = pr.listing_id
                           WHERE l.canonical_key = ? AND l.id != ?
                           ORDER BY pr.checked_at ASC, pr.id ASC""",
                        (ckey, new_listing_id),
                    ).fetchall()
                    seen_ts = set()
                    for h in hist_rows:
                        ts = h["checked_at"]
                        if ts not in seen_ts:
                            seen_ts.add(ts)
                            conn.execute(
                                """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (new_listing_id, h["price"], h["available"], h["coupon_code"], h["final_price"], ts),
                            )
                    if seen_ts:
                        backfilled = True
        except (sqlite3.IntegrityError, DBConflictError):
            raise HTTPException(409, "Esse link já está cadastrado para o produto")

        if not backfilled:
            background.add_task(collect_single_listing, new_listing_id)

        return {"product_id": pid, "listing_id": new_listing_id, "backfilled": backfilled}
    finally:
        conn.close()


@app.get("/api/extension/download")
def download_extension(request: Request):
    """Gera dinamicamente o arquivo .zip da extensão do navegador

    configurado com a URL base deste servidor.
    """
    if not EXTENSION_DIR.is_dir():
        raise HTTPException(status_code=404, detail="Pasta da extensão não encontrada no servidor")

    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    server_origin = f"{proto}://{host}"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(EXTENSION_DIR.rglob("*")):
            if not file_path.is_file():
                continue
            if any(part.startswith(".") for part in file_path.parts):
                continue

            rel_path = file_path.relative_to(EXTENSION_DIR)
            arcname = f"price-tracker-extension/{rel_path.as_posix()}"

            if rel_path.name == "popup.js":
                text = file_path.read_text(encoding="utf-8")
                text = text.replace(
                    "const DEFAULT_SERVER_URL = 'http://localhost:8000';",
                    f"const DEFAULT_SERVER_URL = '{server_origin}';",
                )
                zf.writestr(arcname, text.encode("utf-8"))
            else:
                zf.write(file_path, arcname=arcname)

    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="price-tracker-extension.zip"',
        },
    )


@app.post("/api/listings", status_code=201)
def create_listing(
    body: ListingIn,
    user: dict | None = Depends(get_optional_or_current_user),
):
    conn = get_conn()
    try:
        if user is not None:
            p = conn.execute("SELECT 1 FROM products WHERE id = ? AND user_id = ?", (body.product_id, user["id"])).fetchone()
        else:
            p = conn.execute("SELECT 1 FROM products WHERE id = ?", (body.product_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Produto não encontrado")

        ckey = canonicalize_url(body.url)
        existing = conn.execute(
            """SELECT 1 FROM listings
               WHERE product_id = ?
                 AND (url = ? OR (canonical_key IS NOT NULL AND canonical_key != '' AND canonical_key = ?))""",
            (body.product_id, body.url, ckey),
        ).fetchone()
        if existing:
            raise HTTPException(409, "Esse link já está cadastrado para o produto")

        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO listings (product_id, store, url, css_selector, canonical_key) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (body.product_id, body.store.strip(), body.url, body.css_selector, ckey),
                )
                new_listing_id = cur.lastrowid

                # Backfill imediato de histórico a partir de listings irmãos com a mesma canonical_key
                seen_ts = set()
                if ckey:
                    hist_rows = conn.execute(
                        """SELECT pr.price, pr.available, pr.coupon_code, pr.final_price, pr.checked_at
                           FROM prices pr
                           JOIN listings l ON l.id = pr.listing_id
                           WHERE l.canonical_key = ? AND l.id != ?
                           ORDER BY pr.checked_at ASC, pr.id ASC""",
                        (ckey, new_listing_id),
                    ).fetchall()
                    for h in hist_rows:
                        ts = h["checked_at"]
                        if ts not in seen_ts:
                            seen_ts.add(ts)
                            conn.execute(
                                """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (new_listing_id, h["price"], h["available"], h["coupon_code"], h["final_price"], ts),
                            )
        except (sqlite3.IntegrityError, DBConflictError):
            # só duplicidade vira 409; outros erros sobem como 500 (e aparecem no log)
            raise HTTPException(409, "Esse link já está cadastrado para o produto")
    finally:
        conn.close()
    return {"id": new_listing_id}


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: int, user: dict | None = Depends(get_optional_or_current_user)):
    """Remove um link de loja e seu histórico de preços associado."""
    conn = get_conn()
    try:
        with conn:
            if user is not None:
                l = conn.execute(
                    "SELECT l.id FROM listings l JOIN products p ON p.id = l.product_id WHERE l.id = ? AND p.user_id = ?",
                    (listing_id, user["id"]),
                ).fetchone()
            else:
                l = conn.execute("SELECT id FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not l:
                raise HTTPException(404, "Link não encontrado")

            _preserve_shared_history_before_delete(conn, [listing_id])

            if user is not None:
                cur = conn.execute(
                    "DELETE FROM listings WHERE id = ? AND product_id IN (SELECT id FROM products WHERE user_id = ?)",
                    (listing_id, user["id"]),
                )
            else:
                cur = conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Link não encontrado")
    return {"status": "removido", "id": listing_id}


@app.post("/api/ai/discover-links")
def api_discover_links(
    body: AIDiscoverIn,
    user: dict | None = Depends(get_optional_or_current_user),
):
    product_name = (body.product_name or "").strip()
    category = (body.category or "Notebook").strip()
    exclude_urls: list[str] = []

    conn = get_conn()
    try:
        if body.product_id is not None:
            if user is not None:
                p = conn.execute(
                    "SELECT id, name, category FROM products WHERE id = ? AND user_id = ?",
                    (body.product_id, user["id"]),
                ).fetchone()
            else:
                p = conn.execute(
                    "SELECT id, name, category FROM products WHERE id = ?",
                    (body.product_id,),
                ).fetchone()
            if not p:
                raise HTTPException(404, "Produto não encontrado")
            p = dict(p)

            if not product_name:
                product_name = p["name"]
            if body.category == "Notebook" and p.get("category"):
                category = p["category"]

            existing_listings = conn.execute(
                "SELECT url FROM listings WHERE product_id = ?",
                (body.product_id,),
            ).fetchall()
            exclude_urls = [row["url"] for row in existing_listings if row["url"]]
    finally:
        conn.close()

    if not product_name:
        raise HTTPException(422, "Nome do produto é obrigatório para descoberta")

    from .ai import discover_product_links

    return discover_product_links(
        product_name=product_name,
        category=category,
        target_store=body.store,
        exclude_urls=exclude_urls,
        max_results=6,
    )


@app.post("/api/listings/{listing_id}/heal")
def heal_listing(
    listing_id: int,
    user: dict | None = Depends(get_optional_or_current_user),
):
    conn = get_conn()
    try:
        if user is not None:
            row = conn.execute(
                """SELECT l.id, l.product_id, l.store, l.url, l.css_selector,
                          p.name AS product_name, p.category AS product_category
                   FROM listings l
                   JOIN products p ON p.id = l.product_id
                   WHERE l.id = ? AND p.user_id = ?""",
                (listing_id, user["id"]),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT l.id, l.product_id, l.store, l.url, l.css_selector,
                          p.name AS product_name, p.category AS product_category
                   FROM listings l
                   JOIN products p ON p.id = l.product_id
                   WHERE l.id = ?""",
                (listing_id,),
            ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(404, "Link não encontrado")
    row = dict(row)

    from .ai import heal_expired_listing

    product_name = row["product_name"]
    new_url, healed_res, title = heal_expired_listing(
        product_name=product_name,
        store=row["store"],
        dead_url=row["url"],
        category=row.get("product_category") or "Notebook",
        css_selector=row.get("css_selector"),
    )
    if not new_url or not healed_res or healed_res.price is None:
        raise HTTPException(
            404,
            f"Não foi possível encontrar um novo anúncio ativo para '{product_name}' na loja {row['store']} no momento.",
        )

    new_ckey = canonicalize_url(new_url)
    effective_price = healed_res.final_price or healed_res.price
    checked_at_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    w_conn = get_conn()
    try:
        with w_conn:
            w_conn.execute(
                "UPDATE listings SET url = ?, canonical_key = ? WHERE id = ?",
                (new_url, new_ckey, listing_id),
            )
            w_conn.execute(
                """INSERT INTO prices (listing_id, price, available, coupon_code, final_price, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    listing_id,
                    healed_res.price,
                    int(healed_res.available),
                    healed_res.coupon_code,
                    effective_price,
                    checked_at_utc,
                ),
            )
    finally:
        w_conn.close()

    return {
        "status": "substituido",
        "listing_id": listing_id,
        "old_url": row["url"],
        "new_url": new_url,
        "price": effective_price,
        "title": title,
    }


@app.get("/api/collect/status")
def collect_status():
    status = get_status()
    if not status.get("last_run"):
        conn = get_conn()
        try:
            row = conn.execute("SELECT MAX(checked_at) AS checked_at FROM prices").fetchone()
        finally:
            conn.close()
        if row and row["checked_at"]:
            status["last_run"] = row["checked_at"]
    return status


@app.get("/api/collect/events")
async def collect_events(request: Request):
    """Canal Server-Sent Events (SSE) para notificações em tempo real."""
    # maxsize: um cliente travado não acumula eventos infinitamente na memória
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    add_listener(queue)

    async def event_generator():
        try:
            # Envia estado inicial assim que conecta
            initial_state = get_status()
            yield f"data: {json.dumps({'event': 'status', **initial_state})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Aguarda próximo evento com timeout para checar desconexão
                    data = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # Envia ping / keep-alive
                    yield ": keep-alive\n\n"
        finally:
            remove_listener(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/collect", status_code=202)
def trigger_collect(background: BackgroundTasks):
    if get_status().get("running"):
        return {"status": "coleta já em andamento"}
    background.add_task(collect_all)
    return {"status": "coleta iniciada"}


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_dummy():
    """Silencia requisição automática do Chrome DevTools no terminal."""
    return {}


SPA_ROUTES = {"painel", "produtos", "cadastro", "alertas", "historico", "lojas", "conta"}


@app.get("/{spa_route}")
def spa_route_redirect(spa_route: str):
    route_clean = spa_route.strip("/").lower()
    if route_clean in SPA_ROUTES:
        return RedirectResponse(url=f"/#{route_clean}", status_code=302)
    # Se for um arquivo estático na raiz (ex: index.html, favicon.ico), serve se existir
    candidate = (STATIC_DIR / spa_route).resolve()
    if candidate.is_file() and str(candidate).startswith(str(STATIC_DIR.resolve())):
        return FileResponse(candidate)
    # Qualquer outra rota desconhecida de navegação redireciona para a página inicial "/"
    return RedirectResponse(url="/", status_code=302)


# precisa ficar por último: o mount em "/" captura qualquer rota não definida acima
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
