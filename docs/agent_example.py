"""Reference LiveKit Agents worker for the call agent (for A; call_agent/agent.py stays A's file).

Checked against livekit-agents 1.8.9, livekit-api 1.2.2, livekit-plugins-reson8 0.2.4,
livekit-plugins-elevenlabs 1.8.9, livekit-plugins-anthropic 1.8.9. See docs/agent_example.md.

    python docs/agent_example.py dev
    lk dispatch create --new-room --agent-name jamie-caller --metadata "$(cat fixtures/brief_italian.json)"
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    CloseEvent,
    ConversationItemAddedEvent,
    JobContext,
    UserStateChangedEvent,
    cli,
    llm,
    room_io,
)
from livekit.plugins import anthropic as lk_anthropic
from livekit.plugins import elevenlabs, reson8

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "call_agent"))
load_dotenv(ROOT / ".env")

from config import call_llm_model  # noqa: E402
from contracts_local import CallBrief, CallEvent  # noqa: E402  TODO(hack): switch to server.contracts
from events import EventsClient, mask_phone  # noqa: E402
from prompts import build_instructions, opening_line  # noqa: E402
from tools import Transcript, make_tools  # noqa: E402

log = logging.getLogger("call_agent.worker")

AGENT_NAME = "jamie-caller"      # used by explicit dispatch (server: calls.place_call)
CALLEE_SILENT_S = 5.0            # callee normally speaks first; if not, we greet after this
MAX_CALL_GRACE_S = 20.0          # time to say goodbye after max_call_seconds before hard hang-up

FIRST_TURN_HINT = {
    "de": 'Das ist deine erste Antwort im Telefonat. Beginne mit genau "{line}" und nenne dann das Anliegen in einem Satz.',
    "en": 'This is your first reply on the call. Start with exactly "{line}" and then state the request in one sentence.',
}
TIME_UP_HINT = {
    "de": "Die Gesprächszeit ist um. Fasse in einem Satz zusammen, was vereinbart ist, verabschiede dich und ruf end_call mit outcome partial auf.",
    "en": "Call time is up. Summarise what was agreed in one sentence, say goodbye and call end_call with outcome partial.",
}


class TrackingEvents(EventsClient):
    """EventsClient that remembers whether a final event (result/failed) went out."""

    final_posted = False

    async def post(self, event: CallEvent) -> None:
        if event.type in ("result", "failed"):
            self.final_posted = True
        await super().post(event)


class CallAgent(Agent):
    def __init__(self, brief: CallBrief, events: EventsClient, transcript: Transcript):
        super().__init__(
            instructions=build_instructions(brief),
            tools=make_tools(brief, events, transcript),
        )
        self.brief = brief
        self.greeted = False

    def first_turn_hint(self) -> str:
        return FIRST_TURN_HINT[self.brief.language].format(line=opening_line(self.brief))

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        # The callee spoke first ("Ristorante Roma, guten Tag?"): pin the opening line for this
        # reply only. turn_ctx is per-turn; the Anthropic plugin lifts system messages into `system`.
        if not self.greeted:
            self.greeted = True
            turn_ctx.add_message(role="system", content=self.first_turn_hint())


def _log_turn_latency(item: llm.ChatMessage, last_user: dict) -> None:
    """One line per agent turn from ChatMessage.metrics (metrics_collected is deprecated in 1.8)."""
    m = item.metrics or {}
    if item.role == "user":
        last_user.clear()
        last_user.update(m)
        return

    def f(d: dict, k: str) -> str:
        return f"{d[k]:.2f}s" if k in d else "-"

    log.info(
        "turn latency: end_of_turn %s | stt %s | llm_ttft %s | tts_ttfb %s | e2e %s",
        f(last_user, "end_of_turn_delay"), f(last_user, "transcription_delay"),
        f(m, "llm_node_ttft"), f(m, "tts_node_ttfb"), f(m, "e2e_latency"),
    )


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    brief = CallBrief.model_validate_json(ctx.job.metadata)
    events = TrackingEvents(os.environ["SERVER_PUBLIC_URL"], brief.call_id)
    transcript = Transcript()
    tasks: list[asyncio.Task] = []
    answered = False

    async def on_shutdown() -> None:
        for t in tasks:
            t.cancel()
        if answered and not events.final_posted:
            # The business hung up (or we crashed) before end_call: still report what we have.
            await events.post(CallEvent(
                call_id=brief.call_id, type="result", outcome="partial",
                text="Call ended before the agent finished", transcript=transcript.as_list(),
            ))
        await events.aclose()

    ctx.add_shutdown_callback(on_shutdown)

    allowed = {n.strip() for n in os.getenv("ALLOWED_CALL_NUMBERS", "").split(",") if n.strip()}
    if brief.to_number not in allowed:
        log.error("refusing to dial %s: not in ALLOWED_CALL_NUMBERS", mask_phone(brief.to_number))
        await events.post(CallEvent(call_id=brief.call_id, type="failed", text="Number is not on the allowed list"))
        ctx.shutdown("number not allowed")
        return

    await ctx.connect()

    voice_id = os.environ["ELEVENLABS_VOICE_ID_DE" if brief.language == "de" else "ELEVENLABS_VOICE_ID_EN"]
    session = AgentSession(
        # Reson8 streams to its turn-aware endpoint and decides end of turn server-side.
        stt=reson8.STT(language=brief.language),
        llm=lk_anthropic.LLM(model=call_llm_model()),
        # The plugin reads ELEVEN_API_KEY by default; our .env uses ELEVENLABS_API_KEY.
        tts=elevenlabs.TTS(voice_id=voice_id, api_key=os.environ["ELEVENLABS_API_KEY"], language=brief.language),
        # allow_interruptions= is deprecated in 1.8; this is the replacement.
        turn_handling={"turn_detection": "stt", "interruption": {"enabled": True}},
    )
    agent = CallAgent(brief, events, transcript)
    callee_spoke = asyncio.Event()
    last_user_metrics: dict = {}

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, llm.ChatMessage) or item.role not in ("user", "assistant"):
            return
        transcript.add("business" if item.role == "user" else "agent", item.text_content or "")
        _log_turn_latency(item, last_user_metrics)

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            callee_spoke.set()

    @session.on("close")
    def _on_close(ev: CloseEvent) -> None:
        log.info("session closed: %s", ev.reason)
        ctx.shutdown(f"session closed: {ev.reason}")

    participant_identity = f"sip-{brief.call_id}"
    # Start the session while dialing (as in LiveKit's outbound-caller example). Nothing is said
    # until the callee speaks, because we never call generate_reply here.
    session_started = asyncio.create_task(session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(participant_identity=participant_identity, delete_room_on_close=True),
    ))

    log.info("dialing %s for call %s", mask_phone(brief.to_number), brief.call_id)
    try:
        await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(
            room_name=ctx.room.name,
            sip_trunk_id=os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"],
            sip_call_to=brief.to_number,
            participant_identity=participant_identity,
            wait_until_answered=True,
        ))
    except api.SipCallError as e:  # busy, declined, no answer: carries the SIP status
        log.error("call not answered: SIP %s %s", e.sip_status_code, e.sip_status)
        await events.post(CallEvent(
            call_id=brief.call_id, type="failed", text=f"Call not answered (SIP {e.sip_status_code} {e.sip_status})",
        ))
        ctx.shutdown("sip call failed")
        return
    except api.ServerError as e:  # auth, bad trunk id, validation
        log.error("create_sip_participant failed: %s", e)
        await events.post(CallEvent(call_id=brief.call_id, type="failed", text="Could not place the call"))
        ctx.shutdown("sip error")
        return

    await session_started
    await ctx.wait_for_participant(identity=participant_identity)
    answered = True
    log.info("call answered %s", brief.call_id)
    await events.post(CallEvent(call_id=brief.call_id, type="started", text="Call answered"))
    # TODO(hack): voicemail. livekit.agents.AMD exists in 1.8.9 (must wrap create_sip_participant);
    # see https://docs.livekit.io/telephony/features/answering-machine-detection.md

    async def greet_if_silent() -> None:
        try:
            await asyncio.wait_for(callee_spoke.wait(), CALLEE_SILENT_S)
        except asyncio.TimeoutError:
            if not agent.greeted:
                agent.greeted = True
                log.info("callee silent for %.0fs, greeting first", CALLEE_SILENT_S)
                session.generate_reply(instructions=agent.first_turn_hint())

    async def enforce_max_duration() -> None:
        await asyncio.sleep(brief.max_call_seconds)
        if events.final_posted:
            return
        log.warning("max_call_seconds (%s) reached, asking agent to wrap up", brief.max_call_seconds)
        session.generate_reply(instructions=TIME_UP_HINT[brief.language])
        await asyncio.sleep(MAX_CALL_GRACE_S)
        if not events.final_posted:
            log.warning("hard hang-up after grace period")
            await events.post(CallEvent(
                call_id=brief.call_id, type="result", outcome="partial",
                text="Hung up at max_call_seconds", transcript=transcript.as_list(),
            ))
            await ctx.delete_room()
            ctx.shutdown("max_call_seconds")

    tasks.append(asyncio.create_task(greet_if_silent()))
    tasks.append(asyncio.create_task(enforce_max_duration()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)
