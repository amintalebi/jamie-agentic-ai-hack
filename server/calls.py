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
from fastapi import APIRouter, HTTPException

from common.contracts import CallBrief, CallEvent
from common.masking import mask_phone
from server import gateway, orchestrator, settings

log = logging.getLogger("server.calls")
router = APIRouter()

MAX_ANSWER_WAIT_S = 60


class CallRefused(Exception):
    """The number is not in ALLOWED_CALL_NUMBERS, or the call could not be dispatched."""


@dataclass
class CallState:
    brief: CallBrief
    status: Literal["dialing", "active", "ended"] = "dialing"
    question: CallEvent | None = None            # pending question, None once answered
    answer: str | None = None
    answer_ready: asyncio.Event = field(default_factory=asyncio.Event)


_calls: dict[str, CallState] = {}


# --- dispatchers ---------------------------------------------------------------

class CallDispatcher(Protocol):
    async def dispatch(self, brief: CallBrief) -> None: ...


class LiveKitDispatcher:
    """TODO(D): LiveKit agent dispatch to agent_name=CALL_AGENT_NAME, room=call_id,
    metadata=brief JSON. The call agent creates the SIP participant itself.
    Check https://docs.livekit.io/agents/server/agent-dispatch/ before writing it."""

    async def dispatch(self, brief: CallBrief) -> None:
        raise NotImplementedError


class FakeDispatcher:
    """Plays a scripted call over HTTP against this server, like the real call agent would."""

    async def dispatch(self, brief: CallBrief) -> None:
        asyncio.create_task(self._run(brief))

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
    try:
        await dispatcher.dispatch(brief)
    except Exception as e:
        _calls.pop(brief.call_id, None)
        log.exception("dispatch failed for call %s", brief.call_id)
        raise CallRefused("could not start the call") from e
    return brief.call_id


def get_brief(call_id: str) -> CallBrief | None:
    state = _calls.get(call_id)
    return state.brief if state else None


def active_calls(user_id: str) -> list[CallBrief]:
    """Calls not yet ended, for the orchestrator's context."""
    return [s.brief for s in _calls.values() if s.brief.user_id == user_id and s.status != "ended"]


def pending_question(user_id: str) -> str | None:
    """call_id of the call waiting on this user's answer, or None."""
    for call_id, s in _calls.items():
        if s.brief.user_id == user_id and s.status != "ended" and s.question is not None:
            return call_id
    return None


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
    log.info("answer for call %s: %r", call_id, answer)


async def handle_event(event: CallEvent) -> None:
    state = _calls.get(event.call_id)
    if state is None:
        raise KeyError(event.call_id)
    log.info("call %s event %s: %r", event.call_id, event.type, event.text[:120])
    user = state.brief.user_id

    if event.type == "started":
        state.status = "active"
    elif event.type == "question":
        state.question = event
        state.answer = None
        state.answer_ready.clear()
        await gateway.send_poll(user, f"On the call: {event.text}", event.options or ["Yes", "No"])
    elif event.type in ("result", "failed"):
        state.status = "ended"
        state.question = None
        try:
            await orchestrator.handle_call_result(event)
        except Exception:
            log.exception("handle_call_result failed for call %s", event.call_id)
            await gateway.send_text(user, "The call ended, but I could not write the summary. Sorry!")


# --- HTTP routes used by the call agent ---------------------------------------

@router.post("/calls/{call_id}/events")
async def post_event(call_id: str, event: CallEvent) -> dict:
    if event.call_id != call_id:
        raise HTTPException(400, "call_id in path and body differ")
    try:
        await handle_event(event)
    except KeyError:
        raise HTTPException(404, "unknown call")
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
