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
from dataclasses import dataclass, field

log = logging.getLogger("server.live")

KEEPALIVE_S = 15          # comment line so proxies (nginx: 70 s read timeout) keep the stream open
FORGET_AFTER_S = 600      # drop an ended call's buffer this long after it ended
QUEUE_MAX = 200           # per subscriber; a stuck browser tab is dropped, never blocks the agent


@dataclass
class _Call:
    lines: list[dict] = field(default_factory=list)
    partial: dict | None = None
    ended_at: float | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)


_calls: dict[str, _Call] = {}


def _get(call_id: str) -> _Call:
    _forget_old()
    return _calls.setdefault(call_id, _Call())


def _forget_old() -> None:
    now = time.monotonic()
    for cid in [c for c, s in _calls.items() if s.ended_at and now - s.ended_at > FORGET_AFTER_S and not s.subscribers]:
        del _calls[cid]


def _publish(state: _Call, event: str, data: dict) -> None:
    for q in list(state.subscribers):
        try:
            q.put_nowait((event, data))
        except asyncio.QueueFull:
            state.subscribers.discard(q)  # the generator notices and closes; the browser reconnects


def add_line(call_id: str, speaker: str, text: str, final: bool) -> None:
    """A transcript line from the call agent. Non-final lines are what the business is saying now."""
    text = text.strip()
    if not text:
        return
    state = _get(call_id)
    if state.ended_at:
        return
    line = {"speaker": speaker, "text": text}
    if final:
        if state.partial and state.partial["speaker"] == speaker:
            state.partial = None
        state.lines.append(line)
        _publish(state, "line", line)
    else:
        state.partial = line
        _publish(state, "partial", line)


def end(call_id: str) -> None:
    """The call is over: tell every open dashboard, then stop streaming it."""
    state = _calls.get(call_id)
    if state is None or state.ended_at:
        return
    state.ended_at = time.monotonic()
    state.partial = None
    _publish(state, "end", {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream(call_id: str, already_ended: bool):
    """Async generator of SSE text for one dashboard. Ends when the call ends or the client leaves."""
    state = _get(call_id)
    if already_ended and not state.ended_at:
        state.ended_at = time.monotonic()
    yield _sse("snapshot", {"lines": state.lines, "partial": state.partial, "ended": bool(state.ended_at)})
    if state.ended_at:
        yield _sse("end", {})
        return
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    state.subscribers.add(q)
    try:
        while True:
            if q not in state.subscribers:
                return  # dropped for falling behind
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield _sse(event, data)
            if event == "end":
                return
    finally:
        state.subscribers.discard(q)
