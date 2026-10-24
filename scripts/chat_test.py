"""Text harness for the call conversation: you play the business, Claude replies.

Uses call_agent.prompts.build_instructions(brief). Tools:
  - default: ask_user/end_call stubs; poll answers come from stdin (or U: lines with --script)
  - --server URL: the real call_agent/tools.py (CallTools + EventsClient), e.g. against
    scripts/fake_server.py on :8001, which answers the polls

    python scripts/chat_test.py --brief fixtures/brief_italian.json
    python scripts/chat_test.py --script fixtures/scripts/italian_happy.txt [--model claude-haiku-4-5]
    python scripts/chat_test.py --script fixtures/scripts/italian_happy.txt --server http://localhost:8001

Script format: "B: <business line>", "U: <poll answer>" (queued for the next ask_user),
"# expect ask_user" / "# expect no ask_user" / "# expect say matches <regex>" (about the
preceding B: turn), "# expect end_call outcome=success|partial" and "# expect poll english"
(whole call; checks the polls as posted, so only translated with --server),
"# expect confirm at most once" (whole call: the agent's confirmation question appears 0 or 1 times),
"# expect goodbye" (whole call: the reply that calls end_call also says goodbye),
"# brief: <path>" (default brief). Other "#" lines are comments.
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "call_agent"))

from config import call_llm_model  # noqa: E402
from contracts_local import CallBrief  # noqa: E402  TODO(hack): switch to server.contracts
from prompts import build_instructions  # noqa: E402
from tools import Transcript, looks_german  # noqa: E402

TOOLS = [
    {
        "name": "ask_user",
        "description": (
            "Put the business on hold and ask the user (the person you call for) a question "
            "via a live poll. Use for anything in must_ask_user_for or anything unexpected. "
            "Returns the user's answer, or a note that they did not answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Short question to the user, always in English (shown as an iMessage poll), no dashes."},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2 to 4 answer options in English, 1 to 3 words each, no dashes.",
                },
            },
            "required": ["question", "options"],
        },
    },
    {
        "name": "end_call",
        "description": "End the call after saying goodbye. Report the outcome and the agreed details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["success", "partial", "failed"]},
                "details": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Agreed details, e.g. {\"date\": \"25 Oct\", \"time\": \"21:00\", \"persons\": \"4\", \"name\": \"Sina\"}.",
                },
            },
            "required": ["outcome", "details"],
        },
    },
]

NO_ANSWER = "The user did not answer in time. Say they will call back, say goodbye and call end_call with outcome partial."


def latency_kwargs(model: str, effort: str) -> dict:
    # Haiku 4.5 (default) has no effort param and no thinking by default; newer models get thinking off + low effort.
    if model.startswith("claude-haiku-4"):
        return {}
    return {"thinking": {"type": "disabled"}, "output_config": {"effort": effort}}


# --- tool backends -----------------------------------------------------------

class StubTools:
    """ask_user answers from a queue (script U: lines) or, if none is given, from stdin."""

    def __init__(self, answers: deque | None = None):
        self.answers = answers
        self.polls: list[tuple[str, list[str]]] = []  # raw from the model (no translation here)

    async def ask_user(self, question: str, options: list[str]) -> str:
        self.polls.append((question, options))
        if self.answers is None:
            answer = input("USER ANSWER (empty = timeout)> ").strip()
        else:
            answer = self.answers.popleft() if self.answers else ""
            print(f"USER ANSWER> {answer or '(none, timeout)'}")
        return f"The user answered: {answer}" if answer else NO_ANSWER

    async def end_call(self, outcome: str, details: dict) -> str:
        return "Call ended."


class RealTools:
    """call_agent/tools.py CallTools against a (fake) server."""

    def __init__(self, server: str, brief: CallBrief, transcript: Transcript):
        from events import EventsClient
        from tools import CallTools

        self.events = EventsClient(server, brief.call_id)
        self.impl = CallTools(brief, self.events, transcript)
        self.polls = self.impl.polls  # as posted to the server, after translation

    async def ask_user(self, question: str, options: list[str]) -> str:
        print("  (waiting for answer from server...)")
        out = await self.impl.ask_user(question, options)
        print(f"USER ANSWER> {out}")
        return out

    async def end_call(self, outcome: str, details: dict) -> str:
        return await self.impl.end_call(outcome, details)


# --- conversation ------------------------------------------------------------

@dataclass
class TurnLog:
    said: list[str] = field(default_factory=list)
    asks: int = 0


@dataclass
class CallLog:
    ttfts: list[float] = field(default_factory=list)
    end_outcome: str | None = None
    ended: bool = False
    said: list[str] = field(default_factory=list)  # every agent utterance in the call
    end_said: str = ""  # text spoken in the same reply as end_call


async def run_turn(client, model: str, effort: str, system: str, messages: list,
                   tools, transcript: Transcript, turn: TurnLog, call: CallLog) -> None:
    """Stream assistant replies until it stops calling tools or calls end_call."""
    while True:
        t0 = time.perf_counter()
        ttft = None
        said = []
        async with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
            **latency_kwargs(model, effort),
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start" and ttft is None and event.content_block.type in ("text", "tool_use"):
                    ttft = time.perf_counter() - t0
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    if not said:
                        print("[SAY] ", end="", flush=True)
                    said.append(event.delta.text)
                    print(event.delta.text, end="", flush=True)
            final = await stream.get_final_message()
        if said:
            print()
            turn.said.append("".join(said))
            call.said.append("".join(said))
            transcript.add("agent", "".join(said))
        total = time.perf_counter() - t0
        if ttft is not None:
            call.ttfts.append(ttft)
        print(f"  (ttft {ttft or 0:.2f}s, total {total:.2f}s, out {final.usage.output_tokens} tok, stop {final.stop_reason})")

        messages.append({"role": "assistant", "content": final.content})
        if final.stop_reason != "tool_use":
            return

        results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            if block.name == "ask_user":
                turn.asks += 1
                print(f"[ASK_USER] {block.input.get('question', '')}  options={block.input.get('options', [])}")
                out = await tools.ask_user(block.input.get("question", ""), block.input.get("options", []))
            elif block.name == "end_call":
                outcome = block.input.get("outcome", "failed")
                details = block.input.get("details", {})
                print(f"[END_CALL] outcome={outcome} details={json.dumps(details, ensure_ascii=False)}")
                out = await tools.end_call(outcome, details)
                call.ended, call.end_outcome, call.end_said = True, outcome, "".join(said)
            else:
                out = f"Unknown tool {block.name}"
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})
        if call.ended:
            return


# --- script mode ---------------------------------------------------------------

@dataclass
class ScriptTurn:
    line: str
    answers: list[str] = field(default_factory=list)
    expects: list[str] = field(default_factory=list)


def parse_script(path: Path) -> tuple[str | None, list[ScriptTurn], list[str]]:
    brief_path, turns, call_expects = None, [], []
    for raw in path.read_text().splitlines():
        s = raw.strip()
        if s.startswith("B:"):
            turns.append(ScriptTurn(s[2:].strip()))
        elif s.startswith("U:"):
            turns[-1].answers.append(s[2:].strip())
        elif s.startswith("# brief:"):
            brief_path = s.split(":", 1)[1].strip()
        elif s.startswith("# expect "):
            exp = s[len("# expect "):].strip()
            call_level = exp.startswith(("end_call", "poll", "confirm", "goodbye"))
            (call_expects if call_level else turns[-1].expects).append(exp)
    return brief_path, turns, call_expects


def check_turn(exp: str, turn: TurnLog | None) -> tuple[bool, str]:
    if turn is None:
        return False, "turn not reached (call already ended)"
    said = " ".join(turn.said)
    if exp == "ask_user":
        return turn.asks > 0, f"ask_user calls={turn.asks}"
    if exp == "no ask_user":
        return turn.asks == 0, f"ask_user calls={turn.asks}"
    if exp.startswith("say matches "):
        pattern = exp[len("say matches "):]
        return bool(re.search(pattern, said, re.IGNORECASE)), f"said={said!r}"
    return False, f"unknown expectation {exp!r}"


GOODBYE_RE = re.compile(r"\b(good ?bye|bye|see you|take care)\b|auf wiederh[öo]ren|tsch[üu]ss", re.IGNORECASE)
CONFIRM_RE = re.compile(r"passt (das |es )?so|stimmt (das |es )?so|is that (right|correct)|does that sound right", re.IGNORECASE)


def check_call(exp: str, call: CallLog, polls: list[tuple[str, list[str]]]) -> tuple[bool, str]:
    if exp == "confirm at most once":
        n = sum(len(CONFIRM_RE.findall(t)) for t in call.said)
        return n <= 1, f"confirmation questions={n}"
    if exp == "goodbye":
        return bool(GOODBYE_RE.search(call.end_said)), f"said with end_call={call.end_said!r}"
    if exp == "poll english":
        german = [p for p in polls if looks_german(*p)]
        return not german, f"german polls={german}" if german else f"{len(polls)} polls"
    if not call.ended:
        return False, "end_call never called"
    m = re.match(r"end_call(?:\s+outcome=(\S+))?$", exp)
    if not m:
        return False, f"unknown expectation {exp!r}"
    if m.group(1) is None:
        return True, f"outcome={call.end_outcome}"
    return call.end_outcome in m.group(1).split("|"), f"outcome={call.end_outcome}"


async def run_script(client, model: str, effort: str, brief: CallBrief, script: Path,
                     server: str | None = None) -> dict:
    _, turns, call_expects = parse_script(script)
    transcript = Transcript()
    answers: deque = deque()
    tools = RealTools(server, brief, transcript) if server else StubTools(answers)
    if server and any(t.answers for t in turns):
        print("(note: U: lines are ignored with --server; the server answers the polls)")
    system = build_instructions(brief)
    messages: list = []
    call = CallLog()
    logs: list[TurnLog | None] = []
    print(f"model={model}  brief={brief.call_id}  script={script.name}\n")
    try:
        for t in turns:
            if call.ended:
                logs.append(None)
                continue
            print(f"BUSINESS> {t.line}")
            answers.extend(t.answers)
            transcript.add("business", t.line)
            messages.append({"role": "user", "content": t.line})
            log = TurnLog()
            logs.append(log)
            await run_turn(client, model, effort, system, messages, tools, transcript, log, call)
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
    finally:
        if server:
            await tools.events.aclose()

    results = []
    for t, log in zip(turns, logs + [None] * (len(turns) - len(logs))):
        for exp in t.expects:
            ok, why = check_turn(exp, log)
            results.append((f"{t.line[:40]!r}: {exp}", ok, why))
    for exp in call_expects:
        ok, why = check_call(exp, call, tools.polls)
        results.append((exp, ok, why))

    print("\n-- results --")
    for name, ok, why in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   ({why})"))
    mean_ttft = sum(call.ttfts) / len(call.ttfts) if call.ttfts else 0.0
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} passed, mean ttft {mean_ttft:.2f}s over {len(call.ttfts)} requests")
    return {"passed": passed, "total": len(results), "mean_ttft": mean_ttft,
            "fails": [r for r in results if not r[1]]}


# --- interactive mode ------------------------------------------------------------

async def run_interactive(client, model: str, effort: str, brief: CallBrief, server: str | None) -> None:
    transcript = Transcript()
    tools = RealTools(server, brief, transcript) if server else StubTools()
    system = build_instructions(brief)
    messages: list = []
    call = CallLog()
    print(f"model={model}  brief={brief.call_id}  lang={brief.language}")
    print("You are the business. Type its lines; Ctrl-D to quit.\n")
    try:
        while not call.ended:
            try:
                line = input("BUSINESS> ").strip()
            except EOFError:
                print()
                return
            if not line:
                continue
            transcript.add("business", line)
            messages.append({"role": "user", "content": line})
            try:
                await run_turn(client, model, effort, system, messages, tools, transcript, TurnLog(), call)
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                print(f"[ERROR] {type(e).__name__}: {e}")
                # Drop everything from this turn so the history stays valid; retype the line.
                while messages and not (messages[-1]["role"] == "user" and messages[-1]["content"] == line):
                    messages.pop()
                messages.pop()
        print("\n-- call ended --")
    finally:
        if server:
            await tools.events.aclose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--brief", help="CallBrief JSON (default: the script's '# brief:' or brief_italian.json)")
    p.add_argument("--script", help="run a scripted conversation with assertions")
    p.add_argument("--model", help="default: $CALL_LLM_MODEL or claude-haiku-4-5")
    p.add_argument("--effort", default="low", help="output_config.effort (ignored for claude-haiku-4-5)")
    p.add_argument("--server", help="use the real tools against this server, e.g. http://localhost:8001")
    p.add_argument("--show-prompt", action="store_true", help="print the system prompt and exit")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    model = args.model or call_llm_model()
    script = Path(args.script) if args.script else None
    brief_path = args.brief or (script and parse_script(script)[0]) or "fixtures/brief_italian.json"
    brief_file = Path(brief_path) if Path(brief_path).exists() else ROOT / brief_path
    brief = CallBrief.model_validate_json(brief_file.read_text())
    if args.show_prompt:
        print(build_instructions(brief))
        return

    client = anthropic.AsyncAnthropic(timeout=30.0)
    if script:
        res = asyncio.run(run_script(client, model, args.effort, brief, script, args.server))
        sys.exit(0 if res["passed"] == res["total"] else 1)
    asyncio.run(run_interactive(client, model, args.effort, brief, args.server))


if __name__ == "__main__":
    main()
