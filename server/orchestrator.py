"""Orchestrator (owner: C): LangChain / LangGraph tool loop over the user's messages.

Uses ChatAnthropic (claude-sonnet-4-6 by default, configurable via ORCHESTRATOR_MODEL)
with tools: ask_user, send_message, remember, find_business, place_call.
Includes a deterministic fallback when ANTHROPIC_API_KEY is not configured so local
smoke tests and the demo script work out of the box.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common.contracts import CallBrief, CallEvent
from common.masking import mask_phone
from server import calls, gateway, memory, settings

log = logging.getLogger("server.orchestrator")

MAX_TOOL_ITERATIONS = 6
LLM_TIMEOUT_S = 25.0
DEFAULT_USER_NAME = settings.DEMO_USER_NAME


# Thinking cannot be switched off on these; they take a low effort setting instead.
# On every other model reasoning tokens would eat max_tokens and truncate replies mid-sentence.
NO_THINKING_TOGGLE = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5"}


def _make_llm(max_tokens: int, timeout: float) -> ChatAnthropic:
    """Chat model for the orchestrator, with reasoning off and no temperature (5.x rejects it)."""
    model = settings.ORCHESTRATOR_MODEL
    reasoning: dict = (
        {"reasoning_effort": "low"} if model in NO_THINKING_TOGGLE
        else {"thinking": {"type": "disabled"}}
    )
    return ChatAnthropic(
        model=model,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=1,
        **reasoning,
    )


def _sanitize_text(text: str) -> str:
    """Enforce CLAUDE.md conventions: no em/en dashes, no markdown bold/headers."""
    cleaned = text.replace("\u2014", ", ").replace("\u2013", "-")
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _extract_text(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def _build_system_prompt(
    user_id: str,
    facts: dict[str, str],
    active: list[CallBrief],
) -> str:
    facts_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(facts.items())) or "- (none yet)"
    if active:
        active_lines = "\n".join(
            f"- call_id={b.call_id} to={mask_phone(b.to_number)} goal={b.goal!r}" for b in active
        )
    else:
        active_lines = "- (no active calls)"

    now = datetime.now().astimezone()
    today = now.strftime("%A, %d %b %Y")
    tomorrow = (now + timedelta(days=1)).strftime("%A, %d %b %Y")

    return f"""You are Jamie, a personal assistant that lives in iMessage.
Right now it is {now.strftime("%H:%M")} on {today}. Tomorrow is {tomorrow}.
Resolve relative dates like "tomorrow" or "Friday" against that before putting them in a call goal.

Style rules:
- Write short, warm, natural texting-style replies (1 to 3 short sentences).
- Plain text only: no markdown, no bold, no bullet lists, no numbered lists.
- Never use em dashes or en dashes anywhere in messages or poll options.

Core behavior:
- Check the user's known profile facts first instead of asking again.
- Save any new durable user preference, name, or favorite place using the `remember` tool.
- When the user mentions a business (like "the usual Italian place" or a restaurant name), resolve its phone number using `find_business` and the user's facts (`usual_italian`, etc.).
- Before placing a call with `place_call`, make sure you have all essential details:
  1. Which business and its phone number (via `find_business`).
  2. Date and time.
  3. Party size (number of people) if booking a table.
  4. Seating preference (inside or outside) for restaurant bookings. If the user has not specified inside or outside, ask with `ask_user(question="Inside or outside?", options=["Inside", "Outside"])`.
- Ask at most ONE clarifying question at a time. Always prefer `ask_user` with 2 to 4 short options (1 to 3 words each) over open-ended questions.
- When you call `ask_user`, do NOT call `send_message` in the same turn because `ask_user` already sends the question and poll to the user and ends your turn.
- Never call `place_call` without also sending a one-line message via `send_message` telling the user who you are calling and what for (for example: "Calling Trattoria da Demo now to book a table for 4 tomorrow at 20:00 inside.").
- Do not place a duplicate call if there is already an active call for the same goal.

User phone (user_id): {user_id}

User profile facts:
{facts_lines}

Active calls for this user:
{active_lines}"""


class AskUserArgs(BaseModel):
    question: str = Field(
        description="Short clarifying question to ask the user via an iMessage poll. No dashes."
    )
    options: list[str] = Field(
        description="2 to 4 short answer options (1 to 3 words each, no dashes), e.g. ['Inside', 'Outside']."
    )


class SendMessageArgs(BaseModel):
    text: str = Field(
        description="Short plain-text iMessage to send to the user. No markdown, no em dashes."
    )


class RememberArgs(BaseModel):
    key: str = Field(
        description="Short snake_case fact key, e.g. 'name', 'usual_italian', 'seating_preference', 'dietary'."
    )
    value: str = Field(description="Fact value to remember for this user.")


class FindBusinessArgs(BaseModel):
    query: str = Field(
        description="Search query for business lookup, e.g. 'Trattoria da Demo' or 'italian'."
    )


class PlaceCallArgs(BaseModel):
    to_number: str = Field(description="E.164 phone number of the business from find_business.")
    language: Literal["de", "en"] = Field(
        default="de",
        description="Call language ('de' for German businesses in Berlin, 'en' if English is requested).",
    )
    caller_name: str = Field(description="Name of the user we are calling on behalf of, e.g. 'Sina'.")
    goal: str = Field(
        description="Clear one-line goal in English, e.g. 'Book a table for 4, Sun 25 Oct, 20:00, inside'."
    )
    allowed_decisions: list[str] = Field(
        default_factory=lambda: ["time within 30 min of requested time"],
        description="Decisions the call agent can accept without asking, e.g. ['time within 30 min of 20:00'].",
    )
    must_ask_user_for: list[str] = Field(
        default_factory=lambda: ["any deposit or fee", "different date", "time more than 30 min off"],
        description="Things the call agent must pause and ask the user about via live poll.",
    )


class TurnTracker:
    """Tracks side effects performed by tools during a single orchestrator turn."""

    def __init__(self) -> None:
        self.asked_user = False
        self.sent_message = False
        self.placed_call_id: str | None = None


def _make_turn_tools(user_id: str, facts: dict[str, str], tracker: TurnTracker) -> list:
    @tool("ask_user", args_schema=AskUserArgs)
    async def ask_user_tool(question: str, options: list[str]) -> str:
        """Send an iMessage poll to ask the user a clarifying question. Ends the current turn."""
        q_clean = _sanitize_text(question)
        opts_clean = [_sanitize_text(o) for o in options if _sanitize_text(o)][:4]
        if len(opts_clean) < 2:
            opts_clean = ["Yes", "No"]
        await memory.log_message(
            user_id, "assistant", f"[Poll] {q_clean} ({', '.join(opts_clean)})"
        )
        await gateway.send_poll(user_id, q_clean, opts_clean)
        tracker.asked_user = True
        return "Poll sent to user. Stop now and wait for the user's vote."

    @tool("send_message", args_schema=SendMessageArgs)
    async def send_message_tool(text: str) -> str:
        """Send a short text message to the user on iMessage."""
        clean = _sanitize_text(text)
        if not clean:
            return "Empty message ignored."
        await _reply(user_id, clean)
        tracker.sent_message = True
        return "Message sent."

    @tool("remember", args_schema=RememberArgs)
    async def remember_tool(key: str, value: str) -> str:
        """Save or update a durable fact about the user in memory."""
        k = key.strip()
        v = _sanitize_text(value)
        await memory.remember(user_id, k, v)
        facts[k] = v
        return f"Remembered {k}={v}."

    @tool("find_business", args_schema=FindBusinessArgs)
    async def find_business_tool(query: str) -> str:
        """Look up businesses by name or kind in the seeded businesses directory."""
        hits = await memory.find_business(query)
        return json.dumps(hits, ensure_ascii=False)

    @tool("place_call", args_schema=PlaceCallArgs)
    async def place_call_tool(
        to_number: str,
        caller_name: str,
        goal: str,
        language: Literal["de", "en"] = "de",
        allowed_decisions: list[str] | None = None,
        must_ask_user_for: list[str] | None = None,
    ) -> str:
        """Place a phone call to a business via the voice call agent."""
        merged_facts = dict(facts)
        merged_facts.setdefault("name", caller_name or merged_facts.get("name", DEFAULT_USER_NAME))
        merged_facts.setdefault("booking_name", merged_facts["name"])
        merged_facts.setdefault("callback_number", user_id)

        brief = CallBrief(
            call_id="",
            user_id=user_id,
            to_number=to_number.strip(),
            language=language,
            caller_name=caller_name or merged_facts["name"],
            goal=_sanitize_text(goal),
            user_facts=merged_facts,
            allowed_decisions=[
                _sanitize_text(d)
                for d in (allowed_decisions or ["time within 30 min of requested time"])
            ],
            must_ask_user_for=[
                _sanitize_text(m)
                for m in (
                    must_ask_user_for
                    or ["any deposit or fee", "different date", "time more than 30 min off"]
                )
            ],
        )
        try:
            call_id = await calls.place_call(brief)
        except calls.CallRefused as exc:
            log.warning("place_call refused for user %s: %s", mask_phone(user_id), exc)
            return f"ERROR: CallRefused: {exc}. Tell the user you cannot call that number."
        tracker.placed_call_id = call_id
        return f"Call dispatched with call_id={call_id}."

    return [
        ask_user_tool,
        send_message_tool,
        remember_tool,
        find_business_tool,
        place_call_tool,
    ]


class AgentState(TypedDict):
    messages: list[BaseMessage]
    iterations: int


def _build_agent_graph(llm_with_tools: Any, tools_by_name: dict[str, Any], tracker: TurnTracker):
    async def call_model(state: AgentState) -> dict[str, Any]:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {
            "messages": [*state["messages"], response],
            "iterations": state["iterations"] + 1,
        }

    async def execute_tools(state: AgentState) -> dict[str, Any]:
        last_msg = state["messages"][-1]
        tool_calls = getattr(last_msg, "tool_calls", None) or []
        new_messages: list[BaseMessage] = list(state["messages"])

        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("args") or {}
            call_id = tc.get("id", name)
            tool_fn = tools_by_name.get(name)
            if tool_fn is None:
                result_str = f"Error: unknown tool {name}"
            else:
                try:
                    result_str = await tool_fn.ainvoke(args)
                except Exception as exc:
                    log.exception("tool %s failed", name)
                    result_str = f"Error executing {name}: {exc}"
            new_messages.append(ToolMessage(content=str(result_str), tool_call_id=call_id))
            if tracker.asked_user:
                # Immediately stop executing remaining tools once ask_user has sent a poll
                break

        return {"messages": new_messages, "iterations": state["iterations"]}

    def should_continue_after_agent(state: AgentState) -> str:
        if tracker.asked_user:
            return END
        if state["iterations"] >= MAX_TOOL_ITERATIONS:
            return END
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
            return "tools"
        return END

    def should_continue_after_tools(state: AgentState) -> str:
        if tracker.asked_user:
            return END
        return "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", execute_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue_after_agent, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", should_continue_after_tools, {"agent": "agent", END: END})
    return graph.compile()


def _history_to_langchain(recent: list[dict]) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for item in recent:
        role = item.get("role")
        text = item.get("text", "")
        if not text:
            continue
        if role == "user":
            msgs.append(HumanMessage(content=text))
        else:
            msgs.append(AIMessage(content=text))
    return msgs


async def _run_langgraph_turn(user_id: str, text: str) -> None:
    facts = await memory.get_facts(user_id)
    recent = await memory.recent_messages(user_id, limit=20)
    active = calls.active_calls(user_id)

    tracker = TurnTracker()
    tools = _make_turn_tools(user_id, facts, tracker)
    tools_by_name = {t.name: t for t in tools}

    llm = _make_llm(max_tokens=1000, timeout=LLM_TIMEOUT_S)
    llm_with_tools = llm.bind_tools(tools)
    app = _build_agent_graph(llm_with_tools, tools_by_name, tracker)

    sys_prompt = _build_system_prompt(user_id, facts, active)
    history_msgs = _history_to_langchain(recent)
    if not history_msgs or not isinstance(history_msgs[-1], HumanMessage):
        history_msgs.append(HumanMessage(content=text))

    final_state = await app.ainvoke(
        {"messages": [SystemMessage(content=sys_prompt), *history_msgs], "iterations": 0}
    )

    if tracker.asked_user or tracker.sent_message:
        return

    # If the model ended without calling send_message or ask_user, send its final text response
    for msg in reversed(final_state["messages"]):
        if isinstance(msg, AIMessage):
            reply_text = _sanitize_text(_extract_text(msg.content))
            if reply_text:
                await _reply(user_id, reply_text)
                return
            break

    if tracker.placed_call_id:
        await _reply(user_id, "Calling now, I will text you once I have an update.")


def _booking_brief(user_id: str, facts: dict[str, str], business: dict, seating: str) -> CallBrief:
    """The demo booking brief, dated from the clock so the call agent never has to say "tomorrow"."""
    date_label = (datetime.now().astimezone() + timedelta(days=1)).strftime("%a %d %b")
    return CallBrief(
        call_id="",
        user_id=user_id,
        to_number=business["phone"],
        language="de",
        caller_name=facts.get("name", DEFAULT_USER_NAME),
        goal=f"Book a table for 4, {date_label}, 20:00, {seating}",
        user_facts={**facts, "seating_preference": seating, "callback_number": user_id},
        allowed_decisions=["time within 30 min of 20:00"],
        must_ask_user_for=["any deposit or fee", "different date", "time more than 30 min off"],
    )


async def _place_booking(user_id: str, facts: dict[str, str], seating: str) -> None:
    business = (await memory.find_business(facts.get("usual_italian", "italian")))[0]
    brief = _booking_brief(user_id, facts, business, seating)
    try:
        await calls.place_call(brief)
    except calls.CallRefused as exc:
        await _reply(user_id, f"I cannot call that number: {exc}")
        return
    await _reply(user_id, f"Calling {business['name']} now to {brief.goal[0].lower()}{brief.goal[1:]}.")


async def _fallback_turn(user_id: str, text: str) -> None:
    """Deterministic fallback when ANTHROPIC_API_KEY is not set, supporting the full demo script."""
    facts = await memory.get_facts(user_id)
    low = text.strip().lower()

    # Check if user just answered the "Inside or outside?" poll
    if low in ("inside", "outside", "drinnen", "draußen", "either"):
        seating = "outside" if low in ("outside", "draußen") else "inside"
        await memory.remember(user_id, "seating_preference", seating)
        await _place_booking(user_id, {**facts, "seating_preference": seating}, seating)
        return

    # Check if user is asking to book a table
    if any(w in low for w in ("book", "table", "reserv", "italian", "trattoria", "dinner")):
        has_seating = any(w in low for w in ("inside", "outside", "indoor", "outdoor"))
        if not has_seating:
            q = "Inside or outside?"
            opts = ["Inside", "Outside"]
            await memory.log_message(user_id, "assistant", f"[Poll] {q} (Inside, Outside)")
            await gateway.send_poll(user_id, q, opts)
            return
        seating = "outside" if ("outside" in low or "outdoor" in low) else "inside"
        await _place_booking(user_id, facts, seating)
        return

    await _reply(user_id, f"Got it: {_sanitize_text(text)}")


async def handle_message(user_id: str, text: str) -> None:
    """Run one orchestrator turn for a new user message (text or voice-note transcript)."""
    await memory.log_message(user_id, "user", text)
    if text.strip().lower().startswith("/call"):
        await _demo_call(user_id)
        return

    if not settings.ANTHROPIC_API_KEY:
        log.info("ANTHROPIC_API_KEY not set, running deterministic orchestrator fallback")
        await _fallback_turn(user_id, text)
        return

    try:
        await _run_langgraph_turn(user_id, text)
    except Exception:
        log.exception("LangGraph turn failed for %s, falling back", mask_phone(user_id))
        await _fallback_turn(user_id, text)


async def _remember_durable_call_facts(brief: CallBrief, event: CallEvent) -> None:
    details = event.details or {}
    if event.outcome == "success" and details:
        summary_parts = [f"{k}={v}" for k, v in details.items()]
        await memory.remember(brief.user_id, "last_booking", ", ".join(summary_parts))
    if brief.to_number:
        await memory.remember(brief.user_id, "last_called_number", brief.to_number)


def _fallback_call_summary(brief: CallBrief, event: CallEvent) -> str:
    if event.type == "failed" or event.outcome == "failed":
        return _sanitize_text(f"Sorry, the call did not work out: {event.text}")
    details = event.details or {}
    if event.outcome == "success" and details:
        time_val = details.get("time", "")
        persons_val = details.get("persons", "")
        date_val = details.get("date", "")
        name_val = details.get("name", brief.caller_name)
        parts = ["All set!"]
        booking_bits = []
        if persons_val:
            booking_bits.append(f"table for {persons_val}")
        if date_val:
            booking_bits.append(f"on {date_val}")
        if time_val:
            booking_bits.append(f"at {time_val}")
        if booking_bits:
            parts.append(f"Booked a {' '.join(booking_bits)} under {name_val}.")
        else:
            parts.append(f"{event.text}.")
        if details.get("notes"):
            parts.append(f"Note: {details['notes']}.")
        return _sanitize_text(" ".join(parts))
    return _sanitize_text(f"Call finished ({event.outcome or event.type}): {event.text}")


async def _summarize_call_with_llm(brief: CallBrief, event: CallEvent) -> str:
    if not settings.ANTHROPIC_API_KEY:
        return _fallback_call_summary(brief, event)

    llm = _make_llm(max_tokens=500, timeout=15.0)
    prompt = f"""Write a friendly 2 to 3 line iMessage summary of a phone call you just completed for {brief.caller_name}.
Rules:
- Plain text only, no markdown, no bullets, no em dashes or en dashes.
- State clearly what was agreed (time, party size, date, booking name, any notes) or why it failed.
- Keep it concise and natural.

Call goal: {brief.goal}
Event type: {event.type}
Outcome: {event.outcome}
Summary text: {event.text}
Details: {json.dumps(event.details or {}, ensure_ascii=False)}"""

    try:
        res = await llm.ainvoke([HumanMessage(content=prompt)])
        text = _sanitize_text(_extract_text(res.content))
        return text or _fallback_call_summary(brief, event)
    except Exception:
        log.exception("LLM call summary failed for %s, using fallback", event.call_id)
        return _fallback_call_summary(brief, event)


async def handle_call_result(event: CallEvent) -> None:
    """Called by calls.py for 'result' and 'failed' events: text the user a summary, save the call."""
    brief = calls.get_brief(event.call_id)
    if brief is None:
        log.warning("result for unknown call %s", event.call_id)
        return
    await memory.save_call(brief, event)
    await _remember_durable_call_facts(brief, event)
    summary = await _summarize_call_with_llm(brief, event)
    await _reply(brief.user_id, summary)


async def _reply(user_id: str, text: str) -> None:
    clean = _sanitize_text(text)
    await memory.log_message(user_id, "assistant", clean)
    await gateway.send_text(user_id, clean)


async def _demo_call(user_id: str) -> None:
    facts = await memory.get_facts(user_id)
    await _place_booking(user_id, facts, facts.get("seating_preference", "inside"))
