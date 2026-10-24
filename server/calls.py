"""Call glue (owner: D): place calls, receive CallEvents, mid-call questions.

State lives in memory: the server is a single process and calls last minutes.
The finished call record is saved by the orchestrator through memory.save_call.
"""
import asyncio
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from livekit import api as lk_api

from common.contracts import CallBrief, CallEvent
from common.masking import mask_phone, mask_text
from common.safe_mode import DialPlan, NotDialable, normalize, plan_dial
from server import auth, gateway, history, memory, orchestrator, settings

log = logging.getLogger("server.calls")
router = APIRouter()

MAX_ANSWER_WAIT_S = 60


class CallRefused(Exception):
    """The number may not be dialled (see common/safe_mode.py), or the call could not be dispatched."""


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
    status_updates: int = 0                      # edits this call made to the user's status bubble


_calls: dict[str, CallState] = {}
_background: set[asyncio.Task] = set()  # strong refs so running tasks are not garbage-collected


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


# --- dispatchers ---------------------------------------------------------------

class CallDispatcher(Protocol):
    async def dispatch(self, brief: CallBrief, plan: DialPlan) -> None: ...


class LiveKitDispatcher:
    """Explicit agent dispatch (https://docs.livekit.io/agents/server/agent-dispatch/): the CallBrief
    JSON as job metadata, one new room per call (the dispatch creates it). The call agent registered
    as CALL_AGENT_NAME picks the job up and dials the SIP participant itself.
    LiveKitAPI reads LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET from the environment."""

    TIMEOUT_S = 15

    async def dispatch(self, brief: CallBrief, plan: DialPlan) -> None:
        room = f"call-{brief.call_id}"
        # The brief plus the server's dial decision (common/safe_mode.py); CallBrief ignores the extra keys.
        metadata = json.dumps({**brief.model_dump(), "dial_number": plan.dial_number,
                               "real_business": plan.real_business})
        start = time.monotonic()
        async with lk_api.LiveKitAPI() as lk:
            dispatch = await asyncio.wait_for(lk.agent_dispatch.create_dispatch(lk_api.CreateAgentDispatchRequest(
                agent_name=settings.CALL_AGENT_NAME, room=room, metadata=metadata)), self.TIMEOUT_S)
        log.info("dispatched call %s to agent %s in room %s (dispatch %s, %.0f ms)", brief.call_id,
                 settings.CALL_AGENT_NAME, room, dispatch.id, (time.monotonic() - start) * 1000)


class FakeDispatcher:
    """Plays a scripted call over HTTP against this server, like the real call agent would."""

    async def dispatch(self, brief: CallBrief, plan: DialPlan) -> None:
        _spawn(self._run(brief))

    async def _run(self, brief: CallBrief) -> None:
        base = f"{settings.LOCAL_SERVER_URL}/calls/{brief.call_id}"
        try:
            async with httpx.AsyncClient(timeout=5, headers=agent_headers()) as client:
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
    The brief keeps the business's own number; safe mode only changes what the agent dials
    (see common/safe_mode.py). Raises CallRefused for numbers we may not dial."""
    brief = brief.model_copy(update={"to_number": normalize(brief.to_number)})
    try:
        plan = plan_dial(brief.to_number)
    except NotDialable as e:
        log.warning("refused call to %s: %s", mask_phone(brief.to_number), e)
        raise CallRefused(str(e)) from e
    if plan.real_business:
        log.warning("SAFE_MODE off: dialling the real number %s", mask_phone(plan.dial_number))
    elif plan.dial_number != brief.to_number:
        log.info("SAFE_MODE: call for %s rings stand-in %s", mask_phone(brief.to_number), mask_phone(plan.dial_number))
    if not brief.call_id:
        brief = brief.model_copy(update={"call_id": uuid.uuid4().hex[:12]})
    _calls[brief.call_id] = CallState(brief=brief)
    log.info("placing call %s to %s via %s", brief.call_id, mask_phone(brief.to_number), settings.CALLS_MODE)
    history.call_placed(brief)  # queued first, so this call's later writes find the row
    try:
        await dispatcher.dispatch(brief, plan)
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
    question = state.question
    state.question = None
    state.answer = answer
    state.answer_ready.set()
    log.info("answer for call %s: %r", call_id, mask_text(answer))
    history.answer_received(call_id, answer)
    # A typed answer to "they need your full name" is worth keeping: the next call will not ask again.
    if "full name" in question.text.lower() and answer.lower() not in [o.lower() for o in question.options or []]:
        try:
            await memory.remember(state.brief.user_id, "full_name", answer)
        except Exception:
            log.exception("could not remember full_name for call %s", call_id)


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


# Linq allows 5 edits per status bubble. The orchestrator opens it (a send, not an edit) and writes
# the closing line (1 edit), so a call may make 4. Progress events get at most 2 of those, so the
# mid-call questions always find room.
STATUS_BUDGET = 4
PROGRESS_STATUS_LIMIT = 2


async def _status(state: CallState, text: str) -> None:
    """Best effort edit of the user's status bubble. Never raises: the business is on the line."""
    if state.status_updates >= STATUS_BUDGET:
        log.info("status budget used up for call %s, skipped %r", state.brief.call_id, mask_text(text)[:60])
        return
    state.status_updates += 1
    try:
        await gateway.set_status(state.brief.user_id, text[:100])
    except Exception as e:
        log.warning("status update for call %s failed: %s", state.brief.call_id, e)


async def after_event(event: CallEvent) -> None:
    """Slow side effects of an event (status bubble, polls, LLM summary). Runs after the HTTP reply."""
    state = _calls[event.call_id]
    user = state.brief.user_id
    if event.type == "started":
        await _status(state, "On the phone with them")
    elif event.type == "progress":
        if state.status_updates < PROGRESS_STATUS_LIMIT:
            await _status(state, event.text)
    elif event.type == "question":
        try:
            poll_id = await gateway.send_poll(user, f"On the call: {event.text}", event.options or ["Yes", "No"])
            if state.question is event:  # a newer question may have replaced this one meanwhile
                state.poll_id = poll_id
            await _status(state, f"On hold: {event.text}")  # after the poll: the business is waiting
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

def require_agent(authorization: str = Header(default="")) -> None:
    """The call agent sends CALL_EVENTS_SECRET as a bearer token. Without it anyone who learns a
    call id could post a fake result (texting the user a made-up summary) or read the answer."""
    secret = settings.CALL_EVENTS_SECRET
    if not secret:
        return  # not configured yet: open, see settings.CALL_EVENTS_SECRET
    given = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(401, "bad call agent secret")


def agent_headers() -> dict[str, str]:
    """Headers for requests to the call-agent routes (used by FakeDispatcher)."""
    return {"Authorization": f"Bearer {settings.CALL_EVENTS_SECRET}"} if settings.CALL_EVENTS_SECRET else {}


@router.post("/calls/{call_id}/events", dependencies=[Depends(require_agent)])
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


@router.get("/calls/{call_id}/answer", dependencies=[Depends(require_agent)])
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


MAX_RECORDING_BYTES = 50 * 1024 * 1024  # a 4 min two-track OGG is about 4 MB


@router.post("/calls/{call_id}/recording", dependencies=[Depends(require_agent)])
async def post_recording(call_id: str, request: Request) -> dict:
    """The call agent uploads the session recording (audio/ogg, callee and agent tracks) at hangup."""
    if call_id not in _calls and await history.get_call(call_id) is None:
        raise HTTPException(404, "unknown call")
    content_type = request.headers.get("content-type", "audio/ogg").split(";")[0].strip() or "audio/ogg"
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "empty body")
    if len(audio) > MAX_RECORDING_BYTES:
        raise HTTPException(413, "recording too large")
    await history.save_recording(call_id, content_type, audio)
    log.info("recording for call %s stored: %d bytes %s", call_id, len(audio), content_type)
    return {"ok": True, "bytes": len(audio), "url": f"/calls/{call_id}/recording"}


async def _current_user(authorization: str = Header(default="")) -> str:
    """auth.current_user, looked up per request: auth imports calls (via users and gateway), so
    auth.current_user does not exist yet while this module loads."""
    return await auth.current_user(authorization)


@router.get("/calls/{call_id}/recording")
async def get_recording(call_id: str, user: str = Depends(_current_user)) -> Response:
    """Play or download the recording. Same login as the dashboard; only the call's own user."""
    if await history.get_call(call_id, user) is None:
        raise HTTPException(404, "no recording for this call")
    found = await history.get_recording(call_id)
    if found is None:
        raise HTTPException(404, "no recording for this call")
    content_type, audio = found
    return Response(content=audio, media_type=content_type,
                    headers={"Content-Disposition": f'inline; filename="{call_id}.ogg"'})
