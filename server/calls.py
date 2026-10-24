"""Call glue (owner: D): place calls, receive CallEvents, mid-call questions.

State lives in memory: the server is a single process and calls last minutes.
The finished call record is saved by the orchestrator through memory.save_call.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException

from common.contracts import CallBrief, CallEvent
from common.masking import mask_phone, mask_text
from server import gateway, history, orchestrator, settings

log = logging.getLogger("server.calls")
router = APIRouter()

MAX_ANSWER_WAIT_S = 60


class CallRefused(Exception):
    """The number is not in ALLOWED_CALL_NUMBERS, or the call could not be dispatched."""


class UnknownCall(Exception):
    pass


@dataclass
class CallState:
    brief: CallBrief
    status: Literal["dialing", "active", "ended"] = "dialing"
    question: CallEvent | None = None            # pending question, None once answered or given up
    poll_id: str | None = None                   # id of the poll sent for `question`
    answer: str | None = None                    # None + answer_ready set = "no answer coming"
    answer_ready: asyncio.Event = field(default_factory=asyncio.Event)


_calls: dict[str, CallState] = {}
_background: set[asyncio.Task] = set()  # strong refs so running tasks are not garbage-collected


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


# --- dispatchers ---------------------------------------------------------------

class CallDispatcher(Protocol):
    async def dispatch(self, brief: CallBrief) -> None: ...


class LiveKitDispatcher:
    """Explicit agent dispatch: a new room per call, the brief as job metadata.
    The call agent (call_agent/agent.py, agent_name=CALL_AGENT_NAME) dials the SIP participant itself
    and posts CallEvents back to SERVER_PUBLIC_URL. Reads LIVEKIT_URL, LIVEKIT_API_KEY and
    LIVEKIT_API_SECRET from the environment. Docs: https://docs.livekit.io/agents/server/agent-dispatch/"""

    async def dispatch(self, brief: CallBrief) -> None:
        from livekit import api  # livekit-api; imported here so fake mode never needs it

        async with api.LiveKitAPI() as lk:
            dispatch = await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=settings.CALL_AGENT_NAME,
                    room=f"call-{brief.call_id}",
                    metadata=brief.model_dump_json(),
                )
            )
        log.info("dispatched call %s to agent %s (dispatch %s)", brief.call_id, settings.CALL_AGENT_NAME, dispatch.id)


class FakeDispatcher:
    """Plays a scripted call over HTTP against this server, like the real call agent would."""

    async def dispatch(self, brief: CallBrief) -> None:
        _spawn(self._run(brief))

    async def _run(self, brief: CallBrief) -> None:
        base = f"{settings.LOCAL_SERVER_URL}/calls/{brief.call_id}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                async def post(**kw) -> None:
                    r = await client.post(f"{base}/events", json=CallEvent(call_id=brief.call_id, **kw).model_dump())
                    r.raise_for_status()

                await asyncio.sleep(1)
                await post(type="started", text="Ringing the restaurant")
                await asyncio.sleep(2)
                await post(type="question", text="They offer 21:00 instead of 20:00. OK?", options=["Yes", "No"])
                r = await client.get(f"{base}/answer", params={"wait": 40}, timeout=45)
                answer = r.json().get("answer")
                if answer is None:
                    await post(type="result", text="No answer in time, said we would call back", outcome="partial")
                elif answer.lower().startswith("y"):
                    await post(type="result", text="Table for 4 booked at 21:00", outcome="success",
                               details={"time": "21:00", "persons": "4", "name": brief.caller_name})
                else:
                    await post(type="result", text="Declined 21:00, no booking", outcome="failed")
        except Exception:
            log.exception("fake call %s crashed", brief.call_id)


dispatcher: CallDispatcher = LiveKitDispatcher() if settings.CALLS_MODE == "livekit" else FakeDispatcher()


# --- interface used by gateway and orchestrator --------------------------------

async def place_call(brief: CallBrief) -> str:
    """Dial brief.to_number. Pass call_id="" to get a fresh id. Returns the call_id.
    Raises CallRefused for numbers outside ALLOWED_CALL_NUMBERS."""
    if brief.to_number not in settings.allowed_call_numbers():
        log.warning("refused call to %s: not in ALLOWED_CALL_NUMBERS", mask_phone(brief.to_number))
        raise CallRefused("number is not on the allowed list")
    if not brief.call_id:
        brief = brief.model_copy(update={"call_id": uuid.uuid4().hex[:12]})
    _calls[brief.call_id] = CallState(brief=brief)
    log.info("placing call %s to %s via %s", brief.call_id, mask_phone(brief.to_number), settings.CALLS_MODE)
    history.call_placed(brief)  # queued first, so this call's later writes find the row
    try:
        await dispatcher.dispatch(brief)
    except Exception as e:
        _calls.pop(brief.call_id, None)
        log.exception("dispatch failed for call %s", brief.call_id)
        history.event_received(CallEvent(call_id=brief.call_id, type="failed", text="Could not start the call"))
        raise CallRefused("could not start the call") from e
    return brief.call_id


def get_brief(call_id: str) -> CallBrief | None:
    state = _calls.get(call_id)
    return state.brief if state else None


def active_calls(user_id: str) -> list[CallBrief]:
    """Calls not yet ended, for the orchestrator's context."""
    return [s.brief for s in _calls.values() if s.brief.user_id == user_id and s.status != "ended"]


def _waiting_calls(user_id: str) -> list[tuple[str, CallState]]:
    """Calls of this user that are on hold waiting for the user's answer."""
    return [(cid, s) for cid, s in _calls.items()
            if s.brief.user_id == user_id and s.status != "ended" and s.question is not None]


def pending_question(user_id: str) -> str | None:
    """call_id of a call waiting on this user's answer, or None."""
    waiting = _waiting_calls(user_id)
    return waiting[0][0] if waiting else None


def match_vote(user_id: str, option: str, poll_id: str | None) -> str | None:
    """call_id whose pending question this vote answers, or None (then it is an orchestrator poll vote).

    1. A poll_id that matches a call's poll decides it.
    2. Otherwise fall back to the option, among calls whose poll id we do not know (the messenger gave
       none, or the vote beat us storing it): exactly one such call, and the option is one of its options.
    """
    waiting = _waiting_calls(user_id)
    if poll_id is not None:
        hit = next((cid for cid, s in waiting if s.poll_id == poll_id), None)
        if hit:
            return hit
        waiting = [(cid, s) for cid, s in waiting if s.poll_id is None]
    if len(waiting) != 1:
        return None
    cid, state = waiting[0]
    options = [o.strip().lower() for o in (state.question.options or [])]
    return cid if option.strip().lower() in options else None


async def resolve_answer(call_id: str, answer: str) -> None:
    """Store the user's vote so the call agent's /answer long-poll returns it."""
    state = _calls.get(call_id)
    if state is None or state.question is None:
        log.warning("answer for call %s with no pending question, ignored", call_id)
        return
    # TODO(hack): if the agent already stopped waiting, tell the user it is too late.
    state.question = None
    state.answer = answer
    state.answer_ready.set()
    log.info("answer for call %s: %r", call_id, mask_text(answer))
    history.answer_received(call_id, answer)


def apply_event(event: CallEvent) -> None:
    """Update call state for an event. Fast and side-effect free, so the HTTP reply is immediate."""
    state = _calls.get(event.call_id)
    if state is None:
        raise UnknownCall(event.call_id)
    log.info("call %s event %s: %r", event.call_id, event.type, mask_text(event.text)[:120])
    if event.type == "started":
        state.status = "active"
    elif event.type == "question":
        state.question = event
        state.poll_id = None
        state.answer = None
        state.answer_ready.clear()
    elif event.type in ("result", "failed"):
        state.status = "ended"
        state.question = None


async def after_event(event: CallEvent) -> None:
    """Slow side effects of an event (polls, LLM summary). Runs after the HTTP reply."""
    state = _calls[event.call_id]
    user = state.brief.user_id
    if event.type == "question":
        try:
            poll_id = await gateway.send_poll(user, f"On the call: {event.text}", event.options or ["Yes", "No"])
            if state.question is event:  # a newer question may have replaced this one meanwhile
                state.poll_id = poll_id
        except Exception:
            log.exception("poll for call %s failed, giving up on the question", event.call_id)
            if state.question is event:
                state.question = None
                state.answer = None
                state.answer_ready.set()  # /answer returns null now, the agent says we will call back
            history.note(event.call_id, "poll_failed", "Could not send the poll, the agent will offer a call back")
            try:
                await gateway.send_text(user, f"On the call they asked: {event.text} "
                                              "I could not send you the poll, so I'll say you will call back.")
            except Exception:
                log.exception("fallback text for call %s failed too", event.call_id)
    elif event.type in ("result", "failed"):
        try:
            await orchestrator.handle_call_result(event)
        except Exception:
            log.exception("handle_call_result failed for call %s", event.call_id)
            try:
                await gateway.send_text(user, "The call ended, but I could not write the summary. Sorry!")
            except Exception:
                log.exception("fallback text for call %s failed too", event.call_id)


async def handle_event(event: CallEvent) -> None:
    """apply_event + after_event, inline. For tests and in-process callers."""
    apply_event(event)
    history.event_received(event)
    await after_event(event)


# --- HTTP routes used by the call agent ---------------------------------------

@router.post("/calls/{call_id}/events")
async def post_event(call_id: str, event: CallEvent, background: BackgroundTasks) -> dict:
    if event.call_id != call_id:
        raise HTTPException(400, "call_id in path and body differ")
    try:
        apply_event(event)
    except UnknownCall:
        raise HTTPException(404, "unknown call")
    history.event_received(event)  # queued: a slow database never delays the agent
    background.add_task(after_event, event)
    return {"ok": True}


@router.get("/calls/{call_id}/answer")
async def get_answer(call_id: str, wait: int = 40) -> dict:
    """Long-poll for the user's answer to the last question. {"answer": null} on timeout."""
    state = _calls.get(call_id)
    if state is None:
        raise HTTPException(404, "unknown call")
    try:
        await asyncio.wait_for(state.answer_ready.wait(), timeout=min(max(wait, 0), MAX_ANSWER_WAIT_S))
    except asyncio.TimeoutError:
        return {"answer": None}
    state.answer_ready.clear()
    answer, state.answer = state.answer, None
    return {"answer": answer}
