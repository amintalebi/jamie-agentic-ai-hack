"""Live call transcript, streamed to the dashboard with Server-Sent Events.

The call agent posts each line as it happens (POST /calls/{id}/transcript, see calls.py). This module
keeps the lines of calls in progress in memory and fans them out to every open dashboard
(GET /api/dashboard/calls/{id}/live). The finished transcript is still saved by the result event,
so a restart only loses the live view of calls that were running at the time.

SSE events (each `data:` is JSON):
  snapshot  {"lines": [{speaker, text}], "partial": {speaker, text} | null, "ended": bool}  first, on connect
  line      {"speaker", "text"}       a finished line
  partial   {"speaker", "text"}       what the business is saying right now (replaces the last partial)
  end       {}                        the call is over; the stream closes
"""
import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("server.live")

KEEPALIVE_S = 15          # comment line so proxies (nginx: 70 s read timeout) keep the stream open
END_GRACE_S = 3.0         # lines still arriving after the result (the goodbye) are kept this long
FORGET_AFTER_S = 600      # drop an ended call's buffer this long after it ended
IDLE_LIMIT_S = 1800       # drop any buffer with no new line for this long (crashed or lost calls)
QUEUE_MAX = 200           # per subscriber; a stuck browser tab is dropped, never blocks the agent


@dataclass
class _Call:
    lines: list[dict] = field(default_factory=list)
    partial: dict | None = None
    ending: bool = False             # result received, closing after END_GRACE_S
    ended_at: float | None = None
    last_at: float = field(default_factory=time.monotonic)
    subscribers: set[asyncio.Queue] = field(default_factory=set)


_calls: dict[str, _Call] = {}


def _forget_old() -> None:
    now = time.monotonic()
    for cid, s in list(_calls.items()):
        if s.subscribers:
            continue
        if (s.ended_at and now - s.ended_at > FORGET_AFTER_S) or now - s.last_at > IDLE_LIMIT_S:
            del _calls[cid]


def _publish(state: _Call, event: str, data: dict) -> None:
    for q in list(state.subscribers):
        try:
            q.put_nowait((event, data))
        except asyncio.QueueFull:
            state.subscribers.discard(q)  # the generator notices and closes; the browser reconnects


def add_line(call_id: str, speaker: str, text: str, final: bool) -> None:
    """A transcript line from the call agent (only for calls the server placed, see calls.py).
    Non-final lines are what the business is saying now."""
    text = text.strip()
    if not text:
        return
    _forget_old()
    state = _calls.setdefault(call_id, _Call())
    if state.ended_at:
        return
    state.last_at = time.monotonic()
    line = {"speaker": speaker, "text": text}
    if final:
        if state.partial and state.partial["speaker"] == speaker:
            state.partial = None
        state.lines.append(line)
        _publish(state, "line", line)
    elif not state.ending:
        state.partial = line
        _publish(state, "partial", line)


def end(call_id: str) -> None:
    """The call is over. Lines already on their way (the goodbye) still get END_GRACE_S to arrive,
    then every open dashboard is told and the streams close."""
    state = _calls.get(call_id)
    if state is None or state.ending or state.ended_at:
        return
    state.ending = True
    if state.partial:
        state.partial = None
        _publish(state, "partial", {"speaker": "business", "text": ""})
    try:
        asyncio.get_running_loop().call_later(END_GRACE_S, _finish, call_id)
    except RuntimeError:  # no running loop (tests, scripts): close now
        _finish(call_id)


def _finish(call_id: str) -> None:
    state = _calls.get(call_id)
    if state is None or state.ended_at:
        return
    state.ended_at = time.monotonic()
    state.partial = None
    _publish(state, "end", {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream(call_id: str, is_live: Callable[[], bool]):
    """Async generator of SSE text for one dashboard. Ends when the call ends or the client leaves.
    is_live() asks calls.py whether the call is still running; it never changes shared state here,
    so one viewer cannot end the live view for the others."""
    state = _calls.get(call_id)
    live = is_live()
    if state is None:
        yield _sse("snapshot", {"lines": [], "partial": None, "ended": not live})
        if not live:
            yield _sse("end", {})
            return
        state = _calls.setdefault(call_id, _Call())  # first viewer before the first line
    else:
        ended = bool(state.ended_at) or not live
        yield _sse("snapshot", {"lines": state.lines, "partial": state.partial, "ended": ended})
        if ended:
            yield _sse("end", {})
            return
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    state.subscribers.add(q)
    try:
        while True:
            if q not in state.subscribers:
                return  # dropped for falling behind; the browser reconnects
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                if not is_live() and not state.ending:
                    yield _sse("end", {})  # the call died without a result (crash, lost, restart)
                    return
                yield ": keepalive\n\n"
                continue
            yield _sse(event, data)
            if event == "end":
                return
    finally:
        state.subscribers.discard(q)
