"""LiveKit Agents worker that places the phone call and holds the conversation (role A).

Run (reads .env at the repo root):
    .venv/bin/python call_agent/agent.py dev        # connect to LiveKit Cloud and wait for dispatches
    .venv/bin/python call_agent/agent.py console    # local mic and speaker, no phone; brief from CALL_BRIEF_FILE

Dispatch (server/calls.py does this; scripts/dial_test.py does it for the smoke test):
    lk dispatch create --new-room --agent-name jamie-call-agent --metadata "$(cat fixtures/brief_italian.json)"

Pipeline: ElevenLabs Scribe realtime STT (VAD turn detection; Reson8 behind CALL_STT) -> Claude Haiku -> ElevenLabs TTS, interruptions on.
The callee speaks first. The prompt from prompts.py (role E) makes the LLM answer with the opening
line and the request. If the callee stays silent after pickup, we open after CALL_SILENCE_OPEN_S.

Env, all read after load_dotenv:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_SIP_OUTBOUND_TRUNK_ID
  RESON8_API_KEY, ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID_DE
  ALLOWED_CALL_NUMBERS       comma-separated E.164; anything else is refused before dialing
  SERVER_PUBLIC_URL          where CallEvents go; default http://127.0.0.1:8001 (scripts/fake_server.py)
  CALL_LLM_MODEL             see config.py
  ELEVENLABS_TTS_MODEL       default eleven_v4_turbo
  ELEVENLABS_VOICE_ID_EN     optional; falls back to the DE voice
  CALL_AMD                   1 = answering machine detection before speaking; default 0 for the demo
  CALL_PREEMPTIVE            1 = start the LLM on the eager transcript (lower latency); default 1
  CALL_STT                   elevenlabs (default: Scribe v2 realtime, VAD turn detection) or reson8 (STT turn detection)
  CALL_DIAL_TIMEOUT_S        default 45
  CALL_SILENCE_OPEN_S        default 3; the agent opens after this much silence, or as soon as the callee pauses
  RESON8_EAGER_TURN_PROBABILITY, RESON8_FINAL_TURN_PROBABILITY   optional Reson8 turn-detection thresholds (0..1)
  CALL_BRIEF_FILE            console mode only; default fixtures/brief_italian.json
"""
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))  # flat imports, same as role E's modules
load_dotenv(ROOT / ".env")

from livekit import api, rtc  # noqa: E402
from livekit.agents import (  # noqa: E402
    AMD,
    NOT_GIVEN,
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    JobContext,
    TurnHandlingOptions,
    UserStateChangedEvent,
    cli,
    room_io,
)
from livekit.agents.llm import ChatMessage  # noqa: E402
from livekit.agents.utils import is_given  # noqa: E402
from livekit.plugins import anthropic, elevenlabs, reson8  # noqa: E402

from config import call_llm_model  # noqa: E402
from contracts_local import CallBrief, CallEvent  # noqa: E402  TODO(hack): switch to server.contracts
from events import EventsClient, mask_phone  # noqa: E402
from prompts import build_instructions, opening_line  # noqa: E402
from tools import Transcript, make_tools  # noqa: E402

AGENT_NAME = os.getenv("CALL_AGENT_NAME") or "jamie-call-agent"  # same variable the server uses to dispatch
log = logging.getLogger("call_agent")


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def allowed_numbers() -> set[str]:
    return {n.strip() for n in os.getenv("ALLOWED_CALL_NUMBERS", "").split(",") if n.strip()}


def parse_job(ctx: JobContext) -> tuple[CallBrief, str, str | None]:
    """Returns (brief, mode, server_url). mode is "call", "hello" (dial_test) or "console" (no phone).
    server_url lets dial_test point events at the fake server; the real server never sets it."""
    raw = ctx.job.metadata or ""
    if not raw.strip():
        path = Path(os.getenv("CALL_BRIEF_FILE") or ROOT / "fixtures" / "brief_italian.json")
        log.info("no dispatch metadata, console mode with brief %s", path)
        return CallBrief.model_validate_json(path.read_text()), "console", None
    data = json.loads(raw)
    brief = CallBrief.model_validate(data)
    return brief, ("hello" if data.get("mode") == "hello" else "call"), data.get("server_url")


def hello_instructions(brief: CallBrief) -> str:
    """Tiny prompt for the 14:30 dial test; the booking prompt from role E is used for real calls."""
    if brief.language == "de":
        return (
            f"Du führst einen kurzen Testanruf auf Deutsch für {brief.caller_name}. Das Gegenüber meldet sich zuerst. "
            f'Antworte dann mit "{opening_line(brief)}" und sag, dass das nur ein kurzer Testanruf ist. '
            "Frag, wie der Tag läuft, reagiere in einem kurzen Satz, bedank dich, verabschiede dich und ruf dann "
            "end_call mit outcome success auf. Ein bis zwei kurze Sätze pro Antwort, keine Listen, kein Markdown, "
            "keine Gedankenstriche. Benutze ask_user nicht."
        )
    return (
        f"You are on a short test call in English for {brief.caller_name}. The callee speaks first. "
        f'Reply with "{opening_line(brief)}" and say this is only a quick test call. '
        "Ask how their day is going, react in one short sentence, thank them, say goodbye and then call "
        "end_call with outcome success. One or two short sentences per reply, no lists, no markdown, "
        "no dashes. Do not use ask_user."
    )


class CallLLM(anthropic.LLM):
    """anthropic.LLM that turns extended thinking off on Haiku 5.5.

    Haiku 5.5 thinks by default and the LiveKit plugin does not carry thinking blocks across
    tool calls, so a thinking turn before ask_user/end_call would fail. Measured 24 Oct on this
    laptop (median TTFT, 4 runs): Haiku 4.5 1.47 s, Haiku 5.5 thinking off 1.22 s, both noisy."""

    def __init__(self, **kwargs):
        # Strict tool schemas (the plugin default) make the API reject E's end_call(details: dict[str, str]):
        # "additionalProperties: object is not supported". Non-strict schemas accept it. Seen on the first dial test.
        kwargs.setdefault("_strict_tool_schema", False)
        super().__init__(**kwargs)

    def chat(self, *, extra_kwargs=NOT_GIVEN, **kwargs):
        extra = dict(extra_kwargs) if is_given(extra_kwargs) else {}
        if str(self.model).startswith("claude-haiku-5"):
            extra.setdefault("thinking", {"type": "disabled"})
        return super().chat(extra_kwargs=extra, **kwargs)


def build_session(brief: CallBrief) -> AgentSession:
    voice = os.getenv("ELEVENLABS_VOICE_ID_DE") or ""
    if brief.language == "en" and os.getenv("ELEVENLABS_VOICE_ID_EN"):
        voice = os.environ["ELEVENLABS_VOICE_ID_EN"]
    tts_kwargs = dict(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        model=os.getenv("ELEVENLABS_TTS_MODEL", "eleven_v4_turbo"),  # measured 24 Oct: first audio 0.4 to 0.9 s vs 0.8 to 1.0 s for flash v2.5
        language=brief.language,
    )
    if voice:
        tts_kwargs["voice_id"] = voice
    else:
        log.warning("ELEVENLABS_VOICE_ID_DE not set, using the plugin's default English voice")
    if os.getenv("CALL_STT", "elevenlabs").lower() == "reson8":
        # Reson8 streams with server-side turn detection. On a clean 9 s sample the final came ~1 s after
        # the speaker stopped, but on three real phone calls (24 Oct) it closed the callee's turn only
        # after 10 to 16 s of pauses, so the agent sat silent. Kept behind CALL_STT=reson8.
        knobs = {k: float(v) for k, v in (("eager_turn_probability", os.getenv("RESON8_EAGER_TURN_PROBABILITY")),
                                           ("final_turn_probability", os.getenv("RESON8_FINAL_TURN_PROBABILITY"))) if v}
        stt = reson8.STT(language=brief.language, **knobs)
        turn_detection = "stt"
    else:
        # Default: ElevenLabs Scribe v2 realtime. The bundled Silero VAD decides the end of turn, LiveKit
        # flushes the STT (manual_flush) and the final arrived 0.3 to 0.8 s later in tests, so a turn
        # closes about a second after the callee stops talking, independent of line noise.
        stt = elevenlabs.STT(api_key=os.environ["ELEVENLABS_API_KEY"], model="scribe_v2_realtime", language_code=brief.language)
        turn_detection = "vad"
    return AgentSession(
        stt=stt,
        llm=CallLLM(model=call_llm_model(), max_tokens=200),  # no temperature: Haiku 5.5 rejects it with a 400
        tts=elevenlabs.TTS(**tts_kwargs),
        # The STT (or the bundled Silero VAD) decides the end of turn; the VAD always handles interruptions.
        # preemptive_generation starts the LLM on the eager transcript to cut latency.
        turn_handling=TurnHandlingOptions(
            turn_detection=turn_detection,
            preemptive_generation={"enabled": env_flag("CALL_PREEMPTIVE", True)},
        ),
    )


@dataclass
class CallState:
    answered_at: float | None = None
    user_spoke: bool = False
    user_state: str = "listening"
    callee_paused: asyncio.Event = field(default_factory=asyncio.Event)  # set on speaking -> not speaking
    ended: bool = False          # end_call ran, or we posted a final event ourselves
    turn: int = 0                # assistant turns completed
    opened: bool = False         # we forced the opening line
    last_user_metrics: dict = field(default_factory=dict)


def wire_events(session: AgentSession, ctx: JobContext, brief: CallBrief, transcript: Transcript,
                state: CallState, events: EventsClient, sip_identity: str) -> None:
    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        log.info("callee %s -> %s", ev.old_state, ev.new_state)
        state.user_state = ev.new_state
        if ev.new_state == "speaking":
            state.user_spoke = True
        elif ev.old_state == "speaking":
            state.callee_paused.set()

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, ChatMessage):
            return
        text = (item.text_content or "").strip()
        metrics = dict(item.metrics or {})
        if item.role == "user":
            state.user_spoke = True
            transcript.add("business", text)
            state.last_user_metrics = metrics
            log.info("business: %s", text)
        elif item.role == "assistant":
            state.turn += 1
            transcript.add("agent", text)
            u = state.last_user_metrics

            def f(d: dict, key: str) -> str:
                v = d.get(key)
                return f"{v:.2f}s" if isinstance(v, (int, float)) else "-"

            # One line per turn: end of business speech -> STT final -> LLM first token -> TTS first audio.
            log.info(
                "turn %d | stt_final %s | end_of_turn %s | llm_ttft %s | tts_ttfb %s | e2e %s | agent: %s",
                state.turn, f(u, "transcription_delay"), f(u, "end_of_turn_delay"),
                f(metrics, "llm_node_ttft"), f(metrics, "tts_node_ttfb"), f(metrics, "e2e_latency"), text,
            )

    @session.on("function_tools_executed")
    def _on_tools(ev: FunctionToolsExecutedEvent) -> None:
        names = [getattr(c, "name", "") for c in getattr(ev, "function_calls", [])]
        if names:
            log.info("tools executed: %s", names)
        if "end_call" in names:
            state.ended = True

    @session.on("close")
    def _on_close(ev) -> None:
        log.info("session closed: %s", getattr(ev, "reason", ""))

    @session.on("error")
    def _on_error(ev) -> None:
        log.error("session error: %s recoverable=%s", getattr(ev, "error", ev), getattr(ev, "recoverable", "?"))

    @ctx.room.on("participant_disconnected")
    def _on_disconnected(participant: rtc.RemoteParticipant) -> None:
        if participant.identity != sip_identity or state.ended:
            return
        state.ended = True
        reason = rtc.DisconnectReason.Name(participant.disconnect_reason) if participant.disconnect_reason is not None else "unknown"
        log.warning("business hung up (%s) before end_call", reason)
        asyncio.create_task(post_final(events, brief, transcript, "partial", f"The business hung up ({reason})"))
        asyncio.create_task(graceful_shutdown(ctx, "callee disconnected"))


async def post_final(events: EventsClient, brief: CallBrief, transcript: Transcript, outcome: str, text: str) -> None:
    await events.post(CallEvent(
        call_id=brief.call_id, type="result", text=text, outcome=outcome, details={}, transcript=transcript.as_list(),
    ))


async def post_failed(events: EventsClient, brief: CallBrief, text: str) -> None:
    await events.post(CallEvent(call_id=brief.call_id, type="failed", text=text))


async def graceful_shutdown(ctx: JobContext, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ctx.delete_room()
    ctx.shutdown(reason)


async def open_conversation(session: AgentSession, state: CallState, brief: CallBrief, silence_s: float, max_wait_s: float = 6.0) -> None:
    """Say the first line without waiting for the STT to close the callee's turn.

    First dial tests (24 Oct): the callee greets at pickup and keeps saying "Hallo?" into a silent line,
    so Reson8 never sees a pause and closes the turn only after ~16 s. We open as soon as the callee
    pauses (VAD), or after silence_s if nobody speaks, or after max_wait_s at the latest."""
    waiter = asyncio.create_task(state.callee_paused.wait())
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout=silence_s)
        reason = "callee paused"
    except asyncio.TimeoutError:
        if state.user_state == "speaking":
            try:
                await asyncio.wait_for(asyncio.shield(waiter), timeout=max(max_wait_s - silence_s, 0.1))
                reason = "callee paused"
            except asyncio.TimeoutError:
                reason = f"callee still talking after {max_wait_s:.0f}s"
        else:
            reason = f"silence for {silence_s:.0f}s"
    finally:
        waiter.cancel()
    if state.ended or state.turn > 0 or state.opened:
        return
    state.opened = True
    log.info("opening (%s)", reason)
    session.generate_reply(
        instructions=f'Open the call now with exactly "{opening_line(brief)}" and then state the request in one sentence. '
                     "If the callee is mid-sentence, keep it short.",
        allow_interruptions=False,
    )


async def hard_stop(ctx: JobContext, session: AgentSession, events: EventsClient, brief: CallBrief,
                    transcript: Transcript, state: CallState) -> None:
    """Warn the LLM 25 s before max_call_seconds, then post a partial result and hang up."""
    warn_after = max(brief.max_call_seconds - 25, 10)
    await asyncio.sleep(warn_after)
    if state.ended:
        return
    log.warning("max_call_seconds approaching, asking the agent to wrap up")
    session.generate_reply(
        instructions="Time is almost up. Summarise what was agreed in one sentence, say goodbye, and call end_call now "
                     "with outcome success if the business confirmed, otherwise partial."
    )
    await asyncio.sleep(brief.max_call_seconds - warn_after)
    if state.ended:
        return
    state.ended = True
    log.error("hard stop at max_call_seconds=%d", brief.max_call_seconds)
    await post_final(events, brief, transcript, "partial", f"Hard stop after {brief.max_call_seconds} s")
    await graceful_shutdown(ctx, "max_call_seconds")


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def call_session(ctx: JobContext) -> None:
    brief, mode, server_url = parse_job(ctx)
    ctx.log_context_fields = {"room": ctx.room.name, "call_id": brief.call_id}
    log.info("job mode=%s call=%s to=%s lang=%s goal=%r", mode, brief.call_id, mask_phone(brief.to_number), brief.language, brief.goal[:80])

    server_url = server_url or os.getenv("SERVER_PUBLIC_URL") or "http://127.0.0.1:8001"
    log.info("events go to %s", server_url)
    events = EventsClient(server_url, brief.call_id)
    ctx.add_shutdown_callback(events.aclose)

    trunk_id = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "")
    if mode != "console":
        if brief.to_number not in allowed_numbers():
            log.error("refused: %s is not in ALLOWED_CALL_NUMBERS", mask_phone(brief.to_number))
            await post_failed(events, brief, "Refused: the number is not in the allowed list")
            ctx.shutdown("number not allowed")
            return
        if not trunk_id:
            await post_failed(events, brief, "Server misconfigured: no SIP outbound trunk")
            ctx.shutdown("no trunk")
            return

    transcript = Transcript()
    state = CallState()
    session = build_session(brief)
    instructions = hello_instructions(brief) if mode == "hello" else build_instructions(brief)
    agent = Agent(instructions=instructions, tools=make_tools(brief, events, transcript))
    sip_identity = f"sip-{brief.call_id}"
    wire_events(session, ctx, brief, transcript, state, events, sip_identity)

    # Connect first: RoomOptions(participant_identity=...) reads the local participant inside
    # session.start, which raises "cannot access local participant before connecting" otherwise.
    await ctx.connect()
    # Start the session before dialing so nothing the callee says is missed (LiveKit's outbound
    # examples do the same). The agent stays silent until the callee speaks or open_if_silent fires.
    room_opts = room_io.RoomOptions(participant_identity=sip_identity, close_on_disconnect=True) if mode != "console" else room_io.RoomOptions()
    await session.start(agent=agent, room=ctx.room, room_options=room_opts)

    if mode == "console":
        state.answered_at = time.monotonic()
        asyncio.create_task(open_conversation(session, state, brief, float(os.getenv("CALL_SILENCE_OPEN_S", "3"))))
        asyncio.create_task(hard_stop(ctx, session, events, brief, transcript, state))
        return

    dial_timeout = float(os.getenv("CALL_DIAL_TIMEOUT_S", "45"))
    log.info("dialing %s via trunk %s (timeout %.0fs)", mask_phone(brief.to_number), trunk_id, dial_timeout)
    t_dial = time.monotonic()
    async with contextlib.AsyncExitStack() as stack:
        detector = None
        if env_flag("CALL_AMD", False):
            detector = await stack.enter_async_context(
                AMD(session, participant_identity=sip_identity, ivr_detection=False, suppress_compatibility_warning=True)
            )
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=trunk_id,
                    sip_call_to=brief.to_number,
                    participant_identity=sip_identity,
                    participant_name=f"{brief.caller_name}'s assistant",
                    wait_until_answered=True,
                ),
                timeout=dial_timeout,
            )
        except api.SipCallError as e:
            log.error("call failed: %s %s", e.sip_status_code, e.sip_status)
            await post_failed(events, brief, f"Call not answered ({e.sip_status_code} {e.sip_status})")
            ctx.shutdown("call failed")
            return
        except asyncio.TimeoutError:
            await post_failed(events, brief, f"No answer within {dial_timeout:.0f} s")
            ctx.shutdown("no answer")
            return
        except api.TwirpError as e:
            log.error("SIP request error: %s", e.message)
            await post_failed(events, brief, f"Could not place the call: {e.message}")
            ctx.shutdown("sip error")
            return

        state.answered_at = time.monotonic()
        try:
            await asyncio.wait_for(ctx.wait_for_participant(identity=sip_identity), timeout=10)
        except asyncio.TimeoutError:
            await post_failed(events, brief, "Answered but the callee never joined the room")
            await graceful_shutdown(ctx, "participant missing")
            return
        log.info("answered after %.1fs", state.answered_at - t_dial)
        await events.post(CallEvent(call_id=brief.call_id, type="started", text=f"Call answered by {mask_phone(brief.to_number)}"))

        if detector is not None:
            result = await detector.execute()
            log.info("AMD: %s", result.category)
            if result.category in ("machine-vm", "machine-unavailable"):
                await post_failed(events, brief, "Reached voicemail, no message left")
                await graceful_shutdown(ctx, "voicemail")
                return
            state.user_spoke = state.user_spoke or bool(getattr(result, "transcript", ""))

    asyncio.create_task(open_conversation(session, state, brief, float(os.getenv("CALL_SILENCE_OPEN_S", "3"))))
    asyncio.create_task(hard_stop(ctx, session, events, brief, transcript, state))


if __name__ == "__main__":
    missing = [v for v in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "RESON8_API_KEY",
                           "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY") if not os.getenv(v)]
    if missing:
        print(f"warning: missing in .env: {', '.join(missing)}", file=sys.stderr)
    cli.run_app(server)
