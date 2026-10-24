"""User accounts for the phone login: one row per phone number.

A user is created ("signed up") the first time they verify a login code. `activated_at` is set
once the welcome iMessage went out, so a failed send is retried on the next login.
Postgres when DATABASE_URL is set, otherwise in memory.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Protocol

from psycopg.rows import dict_row

from common.masking import mask_phone
from server import db, gateway, memory

log = logging.getLogger("server.users")

DB_TIMEOUT_S = 10

WELCOME_TEXT = (
    "Hey, it's Eve 👋 Your account is active. "
    "Text me here whenever you want a call made, like \"book a table for 4 tomorrow at 8 at my usual Italian place\". "
    "I'll ask if anything is unclear and text you when it's done."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    phone          text PRIMARY KEY,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz NOT NULL DEFAULT now(),
    activated_at   timestamptz
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserStore(Protocol):
    async def ensure_schema(self) -> None: ...
    async def login(self, phone: str) -> dict: ...
    async def mark_activated(self, phone: str) -> None: ...


class MemoryUsers:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def ensure_schema(self) -> None:
        pass

    async def login(self, phone: str) -> dict:
        now = _now()
        row = self.rows.get(phone)
        if row is None:
            row = self.rows[phone] = {"phone": phone, "created_at": now, "activated_at": None, "new": True}
        else:
            row["new"] = False
        row["last_login_at"] = now
        return dict(row)

    async def mark_activated(self, phone: str) -> None:
        if phone in self.rows:
            self.rows[phone]["activated_at"] = _now()


class PostgresUsers:
    async def ensure_schema(self) -> None:
        async with db.pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def login(self, phone: str) -> dict:
        # xmax = 0 only on a freshly inserted row, which tells signup from login in one statement.
        async with db.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                """INSERT INTO users (phone) VALUES (%s)
                   ON CONFLICT (phone) DO UPDATE SET last_login_at = now()
                   RETURNING phone, created_at, last_login_at, activated_at, (xmax = 0) AS new""",
                (phone,))
            return await cur.fetchone()

    async def mark_activated(self, phone: str) -> None:
        async with db.pool.connection() as conn:
            await conn.execute("UPDATE users SET activated_at = now() WHERE phone = %s AND activated_at IS NULL",
                               (phone,))


store: UserStore = MemoryUsers()


async def init() -> None:
    global store
    store = PostgresUsers() if db.pool is not None else MemoryUsers()
    await store.ensure_schema()
    log.info("users in %s", "postgres" if db.pool is not None else "memory")


async def login(phone: str) -> dict:
    """Create or touch the user. Returns the row plus "new": True on signup.
    If the database is down, login still works but counts as an existing, activated user:
    better to miss a welcome (it goes out on a later login) than to resend it every login."""
    try:
        return await asyncio.wait_for(store.login(phone), DB_TIMEOUT_S)
    except Exception:
        log.exception("user lookup failed for %s, skipping signup and welcome this time", mask_phone(phone))
        return {"phone": phone, "activated_at": "unknown", "new": False}


async def activate(phone: str) -> bool:
    """Send the welcome iMessage (Linq) and mark the user active. Never raises."""
    try:
        await gateway.send_text(phone, WELCOME_TEXT)
    except Exception:
        log.exception("activation message to %s failed, will retry on next login", mask_phone(phone))
        return False
    log.info("activation message sent to %s", mask_phone(phone))
    try:
        await memory.log_message(phone, "assistant", WELCOME_TEXT)
        await asyncio.wait_for(store.mark_activated(phone), DB_TIMEOUT_S)
    except Exception:
        log.exception("could not mark %s as activated", mask_phone(phone))
    return True
