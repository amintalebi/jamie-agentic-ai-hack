"""FastAPI app (owner: B). Mounts the gateway and calls routes; optional Postgres (pgvector).

Run from the repo root: uvicorn server.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool

from server import calls, gateway, settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = None
    if settings.DATABASE_URL:
        pool = AsyncConnectionPool(settings.DATABASE_URL, min_size=1, max_size=5, timeout=10, open=False)
        await pool.open(wait=True, timeout=30)
        async with pool.connection() as conn:
            # The db image's init script also does this; repeating it is harmless and covers external databases.
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        log.info("connected to postgres, pgvector enabled")
        app.state.pool = pool
    else:
        log.warning("DATABASE_URL not set, running without a database")
    log.info("modes: messenger=%s stt=%s calls=%s", settings.MESSENGER_MODE, settings.STT_MODE, settings.CALLS_MODE)
    yield
    if app.state.pool:
        await app.state.pool.close()


app = FastAPI(title="Jamie server", lifespan=lifespan)
app.include_router(gateway.router)
app.include_router(calls.router)
if settings.MESSENGER_MODE == "fake":
    app.include_router(gateway.dev_router)


@app.get("/")
async def hello():
    return {"message": "Hello, world"}


@app.get("/health")
async def health():
    if app.state.pool is None:
        return {"status": "ok", "db": "disabled", "pgvector": None}
    try:
        async with app.state.pool.connection() as conn:
            cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = await cur.fetchone()
    except Exception as exc:
        log.exception("health check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "db": "ok", "pgvector": row[0] if row else None}
