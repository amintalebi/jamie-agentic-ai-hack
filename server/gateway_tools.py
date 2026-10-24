"""iMessage tools for the orchestrator's Sonnet loop (owner: B). The orchestrator decides when to use them.

Wiring in the orchestrator (owner: C), two lines:

    tools = [*own_tools, *gateway_tools.TOOLS]
    ...
    if block.name in gateway_tools.NAMES:
        result = await gateway_tools.run(user_id, block.name, block.input)

`send_message` here is a drop-in for the orchestrator's own send_message: same name and `text`,
plus an optional `effect`. Keep only one of the two in the tool list.
"""
import logging
from datetime import datetime

from common.masking import mask_phone
from server import gateway

log = logging.getLogger("server.gateway_tools")

TOOLS: list[dict] = [
    {
        "name": "send_message",
        "description": (
            "Send the user a text message. Short, friendly, texting style, no markdown. "
            "Optionally add an iMessage effect. Use \"confetti\" or \"celebration\" only when something the "
            "user wanted actually succeeded (e.g. a booking is confirmed), never for questions, progress or "
            "partial results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "effect": {"type": "string", "enum": sorted(gateway.EFFECTS),
                           "description": "Optional iMessage effect. Leave out for normal messages."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "update_status",
        "description": (
            "Show live progress of a call in one status message that updates itself in place, instead of "
            "sending many texts. Start with new=true when a call begins (e.g. \"📞 Calling Trattoria Da Enzo...\"), "
            "then update it as the call goes (\"🗣 On the phone with them\", \"⏸ On hold, they asked about...\", "
            "\"✅ Booked: table for 4 at 21:00\" or \"❌ Couldn't book\"). One line each. At most 5 updates per bubble."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The full new status line."},
                "new": {"type": "boolean", "description": "Start a fresh status bubble (true at the start of a call)."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "send_calendar_invite",
        "description": (
            "Send the user a calendar file they can tap to add an event to their calendar. Only for something "
            "confirmed (a booking the business agreed to), with the confirmed date and time. Consider asking "
            "the user first with ask_user whether they want it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "e.g. \"Dinner at Trattoria Da Enzo (4 people)\""},
                "start": {"type": "string",
                          "description": "Local Berlin date and time, ISO 8601, e.g. \"2026-10-25T21:00\"."},
                "duration_minutes": {"type": "integer", "default": 120},
                "location": {"type": "string", "description": "Business name and address, if known."},
                "notes": {"type": "string", "description": "e.g. the booking name, table preferences."},
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "react",
        "description": (
            "Put a tapback on the user's latest message, e.g. \"love\" or \"like\" to acknowledge a request "
            "right away while you work on it. Use sparingly, at most once per user message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reaction": {"type": "string", "enum": list(gateway.REACTIONS)}},
            "required": ["reaction"],
        },
    },
]

NAMES = {t["name"] for t in TOOLS}


async def run(user_id: str, name: str, args: dict) -> str:
    """Run one tool call for this user. Returns a short result for the tool_result block; on failure a
    line starting with "error:" so the model can recover (e.g. say it in plain text instead)."""
    try:
        if name == "send_message":
            await gateway.send_text(user_id, args["text"], args.get("effect"))
            return "sent"
        if name == "update_status":
            await gateway.set_status(user_id, args["text"], bool(args.get("new", False)))
            return "status updated"
        if name == "send_calendar_invite":
            await gateway.send_calendar_invite(
                user_id, args["title"], datetime.fromisoformat(args["start"]),
                int(args.get("duration_minutes") or 120), args.get("location", ""), args.get("notes", ""))
            return "calendar invite sent"
        if name == "react":
            return "reacted" if await gateway.react(user_id, args["reaction"]) else "no message to react to"
        return f"error: unknown tool {name}"
    except Exception as e:
        log.exception("tool %s for %s failed", name, mask_phone(user_id))
        return f"error: {e}"
