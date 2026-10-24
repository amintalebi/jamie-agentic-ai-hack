"""Call history (owner: D): every call and its event timeline, for the monitoring dashboard.

Postgres when DATABASE_URL is set, otherwise in memory. Writes never block or break a call:
they run in the background, in order per call, with a timeout, and errors are only logged.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from common.contracts import CallBrief, CallEvent
from server import db

log = logging.getLogger("server.history")

WRITE_TIMEOUT_S = 10
LIVE_STATUSES = ("dialing", "active", "waiting_user")
# A live call this long past its max_call_seconds lost contact (agent crashed, events lost).
STALE_GRACE_S = 120

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
CREATE INDEX IF NOT EXISTS calls_user_id ON calls (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS call_events (
    id          bigserial PRIMARY KEY,
    call_id     text NOT NULL REFERENCES calls (call_id) ON DELETE CASCADE,
    type        text NOT NULL,          -- CallEvent.type, plus "placed", "answer", "poll_failed", "lost"
    text        text NOT NULL,
    options     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_events_call_id ON call_events (call_id, id);
"""

LOST_TEXT = "Lost contact: the server restarted during the call"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_stale(call: dict, now: datetime | None = None) -> bool:
    """Still marked live, but far past its time limit."""
    if call["status"] not in LIVE_STATUSES:
        return False
    limit = int(call["brief"].get("max_call_seconds", 240)) + STALE_GRACE_S
    return ((now or _now()) - call["created_at"]).total_seconds() > limit


class HistoryStore(Protocol):
    async def ensure_schema(self) -> None: ...
    async def close_unfinished(self) -> int: ...
    async def add_call(self, brief: CallBrief) -> None: ...
    async def add_event(self, call_id: str, type: str, text: str, options: list[str] | None = None,
                        result: CallEvent | None = None) -> None: ...
    async def list_calls(self, user_id: str, limit: int = 50) -> list[dict]: ...
    async def stats(self, user_id: str, since: datetime) -> dict[str, int]: ...
    async def get_call(self, call_id: str, user_id: str = "") -> dict | None: ...


class MemoryHistory:
    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}

    async def ensure_schema(self) -> None:
        pass

    async def close_unfinished(self) -> int:
        return 0  # memory starts empty

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

    async def list_calls(self, user_id: str, limit: int = 50) -> list[dict]:
        rows = (sorted(self.calls.values(), key=lambda c: c["created_at"], reverse=True)
                if not user_id
                else sorted([c for c in self.calls.values() if c["user_id"] == user_id],
                            key=lambda c: c["created_at"], reverse=True))
        rows = rows[:limit]
        keep = ("call_id", "brief", "status", "outcome", "created_at", "updated_at", "ended_at")
        return [{k: c[k] for k in keep} for c in rows]

    async def stats(self, user_id: str, since: datetime) -> dict[str, int]:
        now = _now()
        calls = [c for c in self.calls.values() if not user_id or c["user_id"] == user_id]
        return {
            "live": sum(c["status"] in LIVE_STATUSES and not is_stale(c, now) for c in calls),
            "recent": sum(c["created_at"] >= since for c in calls),
            "succeeded": sum(c["outcome"] == "success" for c in calls),
            "failed": sum(c["status"] == "failed" or c["outcome"] == "failed" or is_stale(c, now) for c in calls),
        }

    async def get_call(self, call_id: str, user_id: str = "") -> dict | None:
        call = self.calls.get(call_id)
        if call is not None and user_id and call["user_id"] != user_id:
            return None
        return call


# Live-but-past-its-limit, in SQL (same rule as is_stale).
_STALE_SQL = (f"(status IN {LIVE_STATUSES!r} AND created_at < now() - "
              f"(COALESCE((brief->>'max_call_seconds')::int, 240) + {STALE_GRACE_S}) * interval '1 second')")


class PostgresHistory:
    async def ensure_schema(self) -> None:
        async with db.pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def close_unfinished(self) -> int:
        """Calls still live at startup can never finish: their in-memory state is gone."""
        async with db.pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE calls SET status = 'failed', updated_at = now(), ended_at = now()"
                f" WHERE status IN {LIVE_STATUSES!r} RETURNING call_id")
            ids = [r[0] for r in await cur.fetchall()]
            for call_id in ids:
                await conn.execute("INSERT INTO call_events (call_id, type, text) VALUES (%s, 'lost', %s)",
                                   (call_id, LOST_TEXT))
            return len(ids)

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

    async def list_calls(self, user_id: str, limit: int = 50) -> list[dict]:
        async with db.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT call_id, brief, status, outcome, created_at, updated_at, ended_at"
                " FROM calls WHERE %s = '' OR user_id = %s"
                " ORDER BY created_at DESC LIMIT %s", (user_id, user_id, limit))
            return await cur.fetchall()

    async def stats(self, user_id: str, since: datetime) -> dict[str, int]:
        async with db.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                f"SELECT count(*) FILTER (WHERE status IN {LIVE_STATUSES!r} AND NOT {_STALE_SQL}) AS live,"
                f" count(*) FILTER (WHERE created_at >= %s) AS recent,"
                f" count(*) FILTER (WHERE outcome = 'success') AS succeeded,"
                f" count(*) FILTER (WHERE status = 'failed' OR outcome = 'failed' OR {_STALE_SQL}) AS failed"
                f" FROM calls WHERE %s = '' OR user_id = %s", (since, user_id, user_id))
            return dict(await cur.fetchone())

    async def get_call(self, call_id: str, user_id: str = "") -> dict | None:
        async with db.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT * FROM calls WHERE call_id = %s AND (%s = '' OR user_id = %s)",
                              (call_id, user_id, user_id))
            call = await cur.fetchone()
            if call is None:
                return None
            await cur.execute("SELECT type, text, options, created_at FROM call_events"
                              " WHERE call_id = %s ORDER BY id", (call_id,))
            call["events"] = await cur.fetchall()
            return call


store: HistoryStore = MemoryHistory()


async def init() -> None:
    """Pick the store once the db pool is (or is not) open, and close calls a restart orphaned."""
    global store
    store = PostgresHistory() if db.pool is not None else MemoryHistory()
    await store.ensure_schema()
    closed = await store.close_unfinished()
    log.info("call history in %s%s", "postgres" if db.pool is not None else "memory",
             f", closed {closed} calls left unfinished by a restart" if closed else "")


# --- background writer: ordered per call, never blocks the caller --------------

_tails: dict[str, asyncio.Task] = {}   # last queued write per call
_pending: set[asyncio.Task] = set()    # strong refs until done


def _enqueue(call_id: str, what: str, write: Callable[[], Awaitable[None]]) -> None:
    prev = _tails.get(call_id)

    async def run() -> None:
        if prev is not None:
            await asyncio.wait([prev])  # keep this call's timeline in order; prev never raises
        try:
            await asyncio.wait_for(write(), WRITE_TIMEOUT_S)
        except Exception:
            log.exception("history: saving %s for call %s failed", what, call_id)

    task = asyncio.create_task(run())
    _tails[call_id] = task
    _pending.add(task)

    def done(t: asyncio.Task) -> None:
        _pending.discard(t)
        if _tails.get(call_id) is t:
            del _tails[call_id]

    task.add_done_callback(done)


async def flush() -> None:
    """Wait for queued writes (shutdown, tests)."""
    while _pending:
        await asyncio.wait(list(_pending))


def call_placed(brief: CallBrief) -> None:
    _enqueue(brief.call_id, "call", lambda: store.add_call(brief))


def event_received(event: CallEvent) -> None:
    result = event if event.type in ("result", "failed") else None
    _enqueue(event.call_id, f"{event.type} event",
             lambda: store.add_event(event.call_id, event.type, event.text, event.options, result))


def answer_received(call_id: str, answer: str) -> None:
    note(call_id, "answer", answer)


def note(call_id: str, type: str, text: str) -> None:
    """A server-side timeline entry that is not a CallEvent ("answer", "poll_failed")."""
    _enqueue(call_id, type, lambda: store.add_event(call_id, type, text))


# --- reads for the dashboard ---------------------------------------------------

async def list_calls(user_id: str, limit: int = 50) -> list[dict]:
    return await store.list_calls(user_id, limit)


async def stats(user_id: str, since: datetime) -> dict[str, int]:
    return await store.stats(user_id, since)


async def get_call(call_id: str, user_id: str = "") -> dict | None:
    return await store.get_call(call_id, user_id)


def recent_cutoff() -> datetime:
    return _now() - timedelta(hours=24)
