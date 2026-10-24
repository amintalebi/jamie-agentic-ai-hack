"""Orchestrator (owner: C): Claude Sonnet 5.5 tool loop over the user's messages.

Stub behaviour until C lands the tool loop: echo replies, and "/call" places the demo call
so the call flow can be tested end to end.
"""
import logging

from common.contracts import CallBrief, CallEvent
from server import calls, gateway, memory

log = logging.getLogger("server.orchestrator")


async def handle_message(user_id: str, text: str) -> None:
    """Run one orchestrator turn for a new user message (text or voice-note transcript)."""
    await memory.log_message(user_id, "user", text)
    if text.strip().lower().startswith("/call"):
        await _demo_call(user_id)
        return
    # TODO(C): Sonnet tool loop with ask_user, send_message, remember, place_call, find_business.
    await _reply(user_id, f"Got it: {text}")


async def handle_call_result(event: CallEvent) -> None:
    """Called by calls.py for "result" and "failed" events: text the user a summary, save the call."""
    brief = calls.get_brief(event.call_id)
    if brief is None:
        log.warning("result for unknown call %s", event.call_id)
        return
    await memory.save_call(brief, event)
    # TODO(C): 2 to 3 line summary written by Sonnet from event.details; remember durable facts.
    if event.type == "failed":
        await _reply(brief.user_id, f"Sorry, the call did not work out: {event.text}")
    else:
        await _reply(brief.user_id, f"Call done ({event.outcome}): {event.text}")


async def _reply(user_id: str, text: str) -> None:
    await memory.log_message(user_id, "assistant", text)
    await gateway.send_text(user_id, text)


async def _demo_call(user_id: str) -> None:
    facts = await memory.get_facts(user_id)
    business = (await memory.find_business("italian"))[0]
    brief = CallBrief(
        call_id="",
        user_id=user_id,
        to_number=business["phone"],
        language="de",
        caller_name=facts.get("name", "Sina"),
        goal="Book a table for 4, tomorrow, 20:00, inside",
        user_facts=facts,
        allowed_decisions=["time within 30 min of 20:00"],
        must_ask_user_for=["any deposit or fee", "different date", "time more than 30 min off"],
    )
    try:
        await calls.place_call(brief)
    except calls.CallRefused as e:
        await _reply(user_id, f"I can't call that number: {e}")
        return
    await _reply(user_id, f"Calling {business['name']} now to book a table for 4 at 20:00.")
