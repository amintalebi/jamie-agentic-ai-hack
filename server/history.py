"""Call history (owner: D): every call and its event timeline, for the monitoring dashboard.

Postgres when DATABASE_URL is set, otherwise in memory. Writes never break a call:
errors are logged and swallowed.
"""
import logging
from datetime import datetime, timezone
from typing import Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from common.contracts import CallBrief, CallEvent
from server import db

log = logging.getLogger("server.history")

# Dashboard status of a call. "waiting_user" = on hold while the user answers a poll.
STATUS_FOR_EVENT = {"started": "active", "question": "waiting_user", "answer": "active",
                    "poll_failed": "active", "result": "ended", "failed": "failed"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id     text PRIMARY KEY,
    user_id     text NOT NULL,
    brief       jsonb NOT NULL,
    status      text NOT NULL,
    outcome     text,
    summary     text,
    details     jsonb,
    transcript  jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    ended_at    timestamptz
);
CREATE INDEX IF NOT EXISTS calls_created_at ON calls (created_at DESC);
CREATE TABLE IF NOT EXISTS call_events (
    id          bigserial PRIMARY KEY,
    call_id     text NOT NULL REFERENCES calls (call_id) ON DELETE CASCADE,
    type        text NOT NULL,          -- CallEvent.type, plus "placed", "answer", "poll_failed"
    text        text NOT NULL,
    options     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_events_call_id ON call_events (call_id, id);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HistoryStore(Protocol):
    async def ensure_schema(self) -> None: ...
    async def add_call(self, brief: CallBrief) -> None: ...
    async def add_event(self, call_id: str, type: str, text: str, options: list[str] | None = None,
                        result: CallEvent | None = None) -> None: ...
    async def list_calls(self, limit: int = 50) -> list[dict]: ...
    async def get_call(self, call_id: str) -> dict | None: ...


class MemoryHistory:
    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}

    async def ensure_schema(self) -> None:
        pass

    async def add_call(self, brief: CallBrief) -> None:
        now = _now()
        self.calls[brief.call_id] = {
            "call_id": brief.call_id, "user_id": brief.user_id, "brief": brief.model_dump(),
            "status": "dialing", "outcome": None, "summary": None, "details": None, "transcript": None,
            "created_at": now, "updated_at": now, "ended_at": None,
            "events": [{"type": "placed", "text": "Call placed", "options": None, "created_at": now}],
        }

    async def add_event(self, call_id, type, text, options=None, result=None) -> None:
        call = self.calls.get(call_id)
        if call is None:
            return
        now = _now()
        call["events"].append({"type": type, "text": text, "options": options, "created_at": now})
        call["status"] = STATUS_FOR_EVENT.get(type, call["status"])
        call["updated_at"] = now
        if result is not None:
            call.update(outcome=result.outcome, summary=result.text, details=result.details,
                        transcript=result.transcript, ended_at=now)

    async def list_calls(self, limit: int = 50) -> list[dict]:
        rows = sorted(self.calls.values(), key=lambda c: c["created_at"], reverse=True)[:limit]
        return [{k: v for k, v in c.items() if k != "events"} for c in rows]

    async def get_call(self, call_id: str) -> dict | None:
        return self.calls.get(call_id)


class PostgresHistory:
    async def ensure_schema(self) -> None:
        async with db.pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def add_call(self, brief: CallBrief) -> None:
        async with db.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO calls (call_id, user_id, brief, status) VALUES (%s, %s, %s, 'dialing')",
                (brief.call_id, brief.user_id, Jsonb(brief.model_dump())))
            await conn.execute(
                "INSERT INTO call_events (call_id, type, text) VALUES (%s, 'placed', 'Call placed')",
                (brief.call_id,))

    async def add_event(self, call_id, type, text, options=None, result=None) -> None:
        async with db.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO call_events (call_id, type, text, options) VALUES (%s, %s, %s, %s)",
                (call_id, type, text, Jsonb(options) if options is not None else None))
            status = STATUS_FOR_EVENT.get(type)
            if result is not None:
                await conn.execute(
                    "UPDATE calls SET status = %s, outcome = %s, summary = %s, details = %s, transcript = %s,"
                    " updated_at = now(), ended_at = now() WHERE call_id = %s",
                    (status, result.outcome, result.text, Jsonb(result.details), Jsonb(result.transcript), call_id))
            elif status:
                await conn.execute("UPDATE calls SET status = %s, updated_at = now() WHERE call_id = %s",
                                   (status, call_id))

    async def list_calls(self, limit: int = 50) -> list[dict]:
        async with db.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT * FROM calls ORDER BY created_at DESC LIMIT %s", (limit,))
            return await cur.fetchall()

    async def get_call(self, call_id: str) -> dict | None:
        async with db.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT * FROM calls WHERE call_id = %s", (call_id,))
            call = await cur.fetchone()
            if call is None:
                return None
            await cur.execute("SELECT type, text, options, created_at FROM call_events"
                              " WHERE call_id = %s ORDER BY id", (call_id,))
            call["events"] = await cur.fetchall()
            return call


store: HistoryStore = MemoryHistory()


async def init() -> None:
    """Pick the store once the db pool is (or is not) open. Called at startup."""
    global store
    store = PostgresHistory() if db.pool is not None else MemoryHistory()
    await store.ensure_schema()
    log.info("call history in %s", "postgres" if db.pool is not None else "memory")


# --- write helpers used by calls.py: never raise -------------------------------

async def call_placed(brief: CallBrief) -> None:
    try:
        await store.add_call(brief)
    except Exception:
        log.exception("history: saving call %s failed", brief.call_id)


async def event_received(event: CallEvent) -> None:
    try:
        await store.add_event(event.call_id, event.type, event.text, event.options,
                              event if event.type in ("result", "failed") else None)
    except Exception:
        log.exception("history: saving %s event for call %s failed", event.type, event.call_id)


async def answer_received(call_id: str, answer: str) -> None:
    await note(call_id, "answer", answer)


async def note(call_id: str, type: str, text: str) -> None:
    """A server-side timeline entry that is not a CallEvent ("answer", "poll_failed")."""
    try:
        await store.add_event(call_id, type, text)
    except Exception:
        log.exception("history: saving %s for call %s failed", type, call_id)


async def list_calls(limit: int = 50) -> list[dict]:
    return await store.list_calls(limit)


async def get_call(call_id: str) -> dict | None:
    return await store.get_call(call_id)
