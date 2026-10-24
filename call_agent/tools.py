"""ask_user / end_call tools for the call LLM.

CallTools holds the logic and runs without LiveKit (scripts/chat_test.py --server calls it
directly with context=None). make_tools() wraps it as LiveKit function_tools for agent.py.
The model speaks the hold line itself before ask_user (see prompts.py); the tool stays silent.
"""
import logging
from typing import Literal

from livekit.agents import RunContext, function_tool, get_job_context

from contracts_local import CallBrief, CallEvent  # TODO(hack): switch to server.contracts
from events import EventsClient

log = logging.getLogger("call_agent.tools")

ANSWER_WAIT_S = 40
ANSWER_RETRY_S = 5  # one retry, total <= 45 s


class Transcript:
    """Turns of the call; agent.py appends from session events, end_call ships it."""

    def __init__(self) -> None:
        self._turns: list[dict] = []

    def add(self, speaker: Literal["agent", "business"], text: str) -> None:
        if text.strip():
            self._turns.append({"speaker": speaker, "text": text.strip()})

    def as_list(self) -> list[dict]:
        return list(self._turns)


class CallTools:
    def __init__(self, brief: CallBrief, events: EventsClient, transcript: Transcript):
        self.brief = brief
        self.events = events
        self.transcript = transcript
        self.ended = False

    async def ask_user(self, question: str, options: list[str], context: RunContext | None = None) -> str:
        if context is not None:
            context.disallow_interruptions()
        await self.events.post(CallEvent(
            call_id=self.brief.call_id, type="question", text=question, options=options,
        ))
        answer = await self.events.wait_answer(ANSWER_WAIT_S)
        if answer is None:
            answer = await self.events.wait_answer(ANSWER_RETRY_S)
        if answer is None:
            log.warning("ask_user timed out call=%s", self.brief.call_id)
            return (
                f"{self.brief.caller_name} did not answer in time. Tell the business that "
                f"{self.brief.caller_name} will call back, say goodbye and call end_call with outcome partial."
            )
        return f"{self.brief.caller_name} answered: {answer}"

    async def end_call(
        self,
        outcome: Literal["success", "partial", "failed"],
        details: dict[str, str],
        context: RunContext | None = None,
    ) -> str:
        if self.ended:
            return "The call has already ended."
        self.ended = True
        if context is not None:
            await context.wait_for_playout()  # let the goodbye finish before hanging up
        summary = f"{outcome}: " + ", ".join(f"{k}={v}" for k, v in details.items())
        await self.events.post(CallEvent(
            call_id=self.brief.call_id, type="result", text=summary, outcome=outcome,
            details=details, transcript=self.transcript.as_list(),
        ))
        job = get_job_context(required=False)
        if job is not None:  # None in the text harness
            await job.delete_room()
        return "Call ended."


def make_tools(brief: CallBrief, events: EventsClient, transcript: Transcript) -> list:
    """Returns [ask_user, end_call] as LiveKit function_tools."""
    impl = CallTools(brief, events, transcript)

    @function_tool
    async def ask_user(context: RunContext, question: str, options: list[str]) -> str:
        """Put the business on hold and ask the user a question via a live poll.
        Use for anything in must_ask_user_for or anything unexpected.

        Args:
            question: Short question to the user, always in English (shown as an iMessage poll), no dashes.
            options: 2 to 4 answer options in English, 1 to 3 words each, no dashes.
        """
        return await impl.ask_user(question, options, context)

    @function_tool
    async def end_call(
        context: RunContext,
        outcome: Literal["success", "partial", "failed"],
        details: dict[str, str],
    ) -> str:
        """End the call after saying goodbye. Report the outcome and the agreed details.

        Args:
            outcome: success, partial or failed.
            details: Agreed details, e.g. {"date": "25 Oct", "time": "21:00", "persons": "4", "name": "Sina"}.
        """
        return await impl.end_call(outcome, details, context)

    return [ask_user, end_call]
