"""FastAPI app. Hello-world shell with a Postgres (pgvector) connection."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://jamie:jamie@localhost:5432/jamie")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=5, timeout=10, open=False)
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        # The db image's init script also does this; repeating it is harmless and covers external databases.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    log.info("connected to postgres, pgvector enabled")
    app.state.pool = pool
    yield
    await pool.close()


app = FastAPI(title="Jamie server", lifespan=lifespan)


@app.get("/")
async def hello():
    return {"message": "Hello, world"}


@app.get("/health")
async def health():
    try:
        async with app.state.pool.connection() as conn:
            cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = await cur.fetchone()
    except Exception as exc:
        log.exception("health check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "db": "ok", "pgvector": row[0] if row else None}
