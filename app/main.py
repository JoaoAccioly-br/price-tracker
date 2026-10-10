"""API + frontend estático.

    uvicorn app.main:app --reload
"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .collector import add_listener, collect_all, get_status, remove_listener
from .db import get_conn, init_db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Notebook Tracker", lifespan=lifespan)


class ProductIn(BaseModel):
    name: str | None = None
    category: str | None = "Notebook"
    target_price: float | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    target_price: float | None = None


class ListingIn(BaseModel):
    product_id: int
    store: str
    url: str
    css_selector: str | None = None
    use_browser: bool = False


@app.get("/api/products")
def list_products():
    conn = get_conn()
    products = []
    for p in conn.execute("SELECT * FROM products ORDER BY name"):
        listings = conn.execute(
            """SELECT l.id, l.store, l.url,
                 (SELECT price       FROM prices WHERE listing_id = l.id ORDER BY checked_at DESC, id DESC LIMIT 1) AS last_price,
                 (SELECT final_price FROM prices WHERE listing_id = l.id ORDER BY checked_at DESC, id DESC LIMIT 1) AS final_price,
                 (SELECT coupon_code FROM prices WHERE listing_id = l.id ORDER BY checked_at DESC, id DESC LIMIT 1) AS coupon_code,
                 (SELECT available   FROM prices WHERE listing_id = l.id ORDER BY checked_at DESC, id DESC LIMIT 1) AS available,
                 (SELECT checked_at  FROM prices WHERE listing_id = l.id ORDER BY checked_at DESC, id DESC LIMIT 1) AS checked_at
               FROM listings l WHERE l.product_id = ? AND l.active = 1""",
            (p["id"],),
        ).fetchall()
        listings = [dict(r) for r in listings]
        in_stock_listings = [
            l for l in listings
            if l["last_price"] and l["available"]
        ]
        best_l = None
        best_p = None
        if in_stock_listings:
            best_l = min(
                in_stock_listings,
                key=lambda x: (x["final_price"] if x.get("final_price") is not None else x["last_price"]),
            )
            best_p = best_l["final_price"] if best_l.get("final_price") is not None else best_l["last_price"]

        products.append({
            **dict(p),
            "listings": listings,
            "best_price": best_p,
            "best_store": best_l["store"] if best_l else None,
            "best_listing": best_l,
        })
    conn.close()
    return products


@app.get("/api/products/{product_id}/history")
def price_history(product_id: int):
    conn = get_conn()
    rows = conn.execute(
        """SELECT l.store, pr.price, pr.final_price, pr.coupon_code, pr.available, pr.checked_at
           FROM prices pr JOIN listings l ON l.id = pr.listing_id
           WHERE l.product_id = ? ORDER BY pr.checked_at""",
        (product_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/history")
def full_history(limit: int = 50):
    """Retorna as últimas leituras de preços em todas as lojas para a tela de Histórico."""
    conn = get_conn()
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
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/products", status_code=201)
def create_product(body: ProductIn):
    if not body.name:
        raise HTTPException(422, "Nome do produto é obrigatório")
    cat = (body.category or "Notebook").strip()
    conn = get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO products (name, category, target_price) VALUES (?, ?, ?)",
            (body.name, cat, body.target_price),
        )
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
        set_clauses.append("name = ?")
        params.append(update_data["name"])
    if "category" in update_data:
        set_clauses.append("category = ?")
        params.append(update_data["category"])
    if "target_price" in update_data:
        set_clauses.append("target_price = ?")
        params.append(update_data["target_price"])

    params.append(product_id)
    query = f"UPDATE products SET {', '.join(set_clauses)} WHERE id = ?"
    conn = get_conn()
    with conn:
        cur = conn.execute(query, params)
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Produto não encontrado")
    return {"status": "atualizado", "id": product_id}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    """Remove um produto e seus listings/histórico associados (ON DELETE CASCADE)."""
    conn = get_conn()
    with conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Produto não encontrado")
    return {"status": "removido", "id": product_id}


@app.post("/api/listings", status_code=201)
def create_listing(body: ListingIn):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM products WHERE id = ?", (body.product_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Produto não encontrado")
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO listings (product_id, store, url, css_selector, use_browser) "
                "VALUES (?, ?, ?, ?, ?)",
                (body.product_id, body.store, body.url, body.css_selector, int(body.use_browser)),
            )
    except Exception:
        raise HTTPException(409, "Esse link já está cadastrado para o produto")
    finally:
        conn.close()
    return {"id": cur.lastrowid}


@app.get("/api/collect/status")
def collect_status():
    status = get_status()
    if not status.get("last_run"):
        conn = get_conn()
        row = conn.execute("SELECT checked_at FROM prices ORDER BY checked_at DESC, id DESC LIMIT 1").fetchone()
        conn.close()
        if row and row["checked_at"]:
            status["last_run"] = row["checked_at"]
    return status


@app.get("/api/collect/events")
async def collect_events(request: Request):
    """Canal Server-Sent Events (SSE) para notificações em tempo real."""
    queue: asyncio.Queue = asyncio.Queue()
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
    background.add_task(collect_all)
    return {"status": "coleta iniciada"}


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_dummy():
    """Silencia requisição automática do Chrome DevTools no terminal."""
    return {}


# precisa ficar por último: o mount em "/" captura qualquer rota não definida acima
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
