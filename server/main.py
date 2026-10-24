"""FastAPI app (owner: B). Mounts the gateway, calls and dashboard routes; optional Postgres (pgvector).

Run from the repo root: uvicorn server.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from server import auth, calls, dashboard, db, gateway, history, settings, users

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool()
    await history.init()
    await users.init()
    log.info("modes: messenger=%s stt=%s calls=%s sms=%s", settings.MESSENGER_MODE, settings.STT_MODE,
             settings.CALLS_MODE, settings.SMS_MODE)
    await auth.check_connection()
    yield
    await history.flush()
    await db.close_pool()


app = FastAPI(title="Jamie server", lifespan=lifespan)
app.include_router(gateway.router)
app.include_router(calls.router)
app.include_router(dashboard.router)
app.include_router(auth.router)
if settings.MESSENGER_MODE == "fake":
    app.include_router(gateway.dev_router)
dashboard.mount_app(app)  # the PWA at /app/, if web/dist is built


@app.get("/")
async def hello():
    return {"message": "Hello, world"}


@app.get("/health")
async def health():
    if db.pool is None:
        return {"status": "ok", "db": "disabled", "pgvector": None}
    try:
        async with db.pool.connection() as conn:
            cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = await cur.fetchone()
    except Exception as exc:
        log.exception("health check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "db": "ok", "pgvector": row[0] if row else None}
