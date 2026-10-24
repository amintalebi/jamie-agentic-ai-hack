"""Shared Postgres pool. `pool` is None when DATABASE_URL is empty (fakes only)."""
import logging

from psycopg_pool import AsyncConnectionPool

from server import settings

log = logging.getLogger("server.db")

pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global pool
    if not settings.DATABASE_URL:
        log.warning("DATABASE_URL not set, running without a database")
        return
    p = AsyncConnectionPool(settings.DATABASE_URL, min_size=1, max_size=5, timeout=10, open=False)
    await p.open(wait=True, timeout=30)
    async with p.connection() as conn:
        # The db image's init script also does this; repeating it is harmless and covers external databases.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    log.info("connected to postgres, pgvector enabled")
    pool = p


async def close_pool() -> None:
    global pool
    if pool is not None:
        await pool.close()
        pool = None
