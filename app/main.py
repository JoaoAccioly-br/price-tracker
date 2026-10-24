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
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .analyzer import analyze_price_history, get_product_analysis
from .collector import add_listener, collect_all, get_status, remove_listener
from .db import DBConflictError, get_conn, init_db
from .scrapers.base import UnsafeURLError, validate_url_host

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "").lower() in ("1", "true", "yes")

# Rotas liberadas sem autenticação (healthcheck do Docker)
PUBLIC_PATHS = {"/api/health"}


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
    # 1) Autenticação fail-closed: ativa se APP_USERNAME OU APP_PASSWORD estiverem definidos
    if (APP_USERNAME or APP_PASSWORD) and request.url.path not in PUBLIC_PATHS:
        if not _is_authorized(request.headers.get("authorization")):
            return PlainTextResponse(
                "Autenticação necessária", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="price-tracker", charset="UTF-8"'},
            )

    response = await call_next(request)

    # 2) Cabeçalhos de segurança (defesa em profundidade)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")  # impede clickjacking via iframe
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
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


# ----------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    """Healthcheck leve (sem consultar o banco) usado pelo Docker."""
    return {"status": "ok"}


@app.get("/api/products")
def list_products():
    conn = get_conn()
    try:
        products = [dict(p) for p in conn.execute("SELECT * FROM products ORDER BY name")]
        # Uma única consulta para todos os listings + última leitura de cada um
        # (antes: 1 consulta por produto, com 5 subconsultas por listing — N+1).
        rows = conn.execute(
            """WITH latest AS (
                   SELECT listing_id, price, final_price, coupon_code, available, checked_at,
                          ROW_NUMBER() OVER (PARTITION BY listing_id
                                             ORDER BY checked_at DESC, id DESC) AS rn
                   FROM prices
               )
               SELECT l.id, l.product_id, l.store, l.url,
                      lt.price AS last_price, lt.final_price, lt.coupon_code,
                      lt.available, lt.checked_at
               FROM listings l
               LEFT JOIN latest lt ON lt.listing_id = l.id AND lt.rn = 1
                WHERE l.active = 1
                ORDER BY l.id"""
        ).fetchall()

        # Leituras históricas dos últimos 65 dias para cálculo de métricas de 30 e 60 dias
        # (evita Full Table Scan e consumo excessivo de RAM com histórico antigo)
        cutoff_65d = (datetime.now(timezone.utc) - timedelta(days=65)).strftime("%Y-%m-%d %H:%M:%S")
        price_rows = conn.execute(
            """SELECT l.product_id, pr.price, pr.final_price, pr.checked_at
               FROM prices pr
               JOIN listings l ON l.id = pr.listing_id
               WHERE (pr.available = 1 OR pr.available IS NULL)
                 AND pr.checked_at >= ?
               ORDER BY pr.checked_at ASC""",
            (cutoff_65d,),
        ).fetchall()
    finally:
        conn.close()

    by_product: dict[int, list[dict]] = {}
    for r in rows:
        d = dict(r)
        by_product.setdefault(d.pop("product_id"), []).append(d)

    prices_by_product: dict[int, list[dict]] = {}
    for pr_r in price_rows:
        d = dict(pr_r)
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
def product_analysis(product_id: int):
    """Retorna análise detalhada de 30 e 60 dias para detecção de promoção falsa."""
    conn = get_conn()
    try:
        p = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Produto não encontrado")
        return get_product_analysis(product_id, conn=conn).to_dict()
    finally:
        conn.close()


@app.get("/api/products/{product_id}/history")
def price_history(product_id: int, limit: int = Query(2000, ge=1, le=10000)):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT store, price, final_price, coupon_code, available, checked_at
               FROM (
                   SELECT pr.id, l.store, pr.price, pr.final_price, pr.coupon_code, pr.available, pr.checked_at
                   FROM prices pr JOIN listings l ON l.id = pr.listing_id
                   WHERE l.product_id = ?
                   ORDER BY pr.checked_at DESC, pr.id DESC
                   LIMIT ?
               ) sub
               ORDER BY checked_at ASC, id ASC""",
            (product_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/api/history")
def full_history(limit: int = Query(50, ge=1, le=1000)):
    """Retorna as últimas leituras de preços em todas as lojas para a tela de Histórico."""
    conn = get_conn()
    try:
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
def create_product(body: ProductIn):
    if not body.name or not body.name.strip():
        raise HTTPException(422, "Nome do produto é obrigatório")
    cat = (body.category or "Notebook").strip() or "Notebook"
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO products (name, category, target_price) VALUES (?, ?, ?)",
                (body.name.strip(), cat, body.target_price),
            )
    finally:
        conn.close()
    return {"id": cur.lastrowid}


@app.patch("/api/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate):
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
    # set_clauses só contém literais fixos acima -> sem risco de SQL injection
    query = f"UPDATE products SET {', '.join(set_clauses)} WHERE id = ?"
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute(query, params)
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Produto não encontrado")
    return {"status": "atualizado", "id": product_id}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    """Remove um produto e seus listings/histórico associados (ON DELETE CASCADE)."""
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Produto não encontrado")
    return {"status": "removido", "id": product_id}


@app.post("/api/listings", status_code=201)
def create_listing(body: ListingIn):
    conn = get_conn()
    try:
        if not conn.execute("SELECT 1 FROM products WHERE id = ?", (body.product_id,)).fetchone():
            raise HTTPException(404, "Produto não encontrado")
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO listings (product_id, store, url, css_selector) "
                    "VALUES (?, ?, ?, ?)",
                    (body.product_id, body.store.strip(), body.url, body.css_selector),
                )
        except (sqlite3.IntegrityError, DBConflictError):
            # só duplicidade vira 409; outros erros sobem como 500 (e aparecem no log)
            raise HTTPException(409, "Esse link já está cadastrado para o produto")
    finally:
        conn.close()
    return {"id": cur.lastrowid}


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: int):
    """Remove um link de loja e seu histórico de preços associado."""
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Link não encontrado")
    return {"status": "removido", "id": listing_id}


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


# precisa ficar por último: o mount em "/" captura qualquer rota não definida acima
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
