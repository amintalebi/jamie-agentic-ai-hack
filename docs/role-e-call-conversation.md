# Role E: Call conversation. Work plan and isolation testing

Owner: Ehsan (ETohidi). Branch: `feat/call-conversation`.
Owns: `call_agent/prompts.py`, `call_agent/tools.py`, `call_agent/events.py`,
plus the dev-only `scripts/chat_test.py` and `scripts/fake_server.py`.
Do not touch `call_agent/agent.py` (A) or `server/` (B, C, D).

## Goal

Given a `CallBrief`, produce (1) the system prompt the call LLM uses, (2) the two
tools `ask_user` and `end_call`, (3) the HTTP client that posts `CallEvent`s to the
server. Everything must be testable with no phone, no LiveKit, no real server.

## Public interface (agreed with A)

```python
# call_agent/prompts.py
def build_instructions(brief: CallBrief) -> str
def opening_line(brief: CallBrief) -> str          # what to say right after the callee answers

# call_agent/tools.py
def make_tools(brief: CallBrief, events: EventsClient, transcript: Transcript) -> list
#   returns [ask_user, end_call] as LiveKit function_tools

# call_agent/events.py
class EventsClient:
    def __init__(self, server_url: str, call_id: str, timeout: float = 5.0)
    async def post(self, event: CallEvent) -> None       # POST {server_url}/calls/{call_id}/events
    async def wait_answer(self, wait: int = 40) -> str | None   # GET {server_url}/calls/{call_id}/answer?wait=40
```

`Transcript` is a tiny list-like helper with `.add(speaker, text)` and `.as_list()`;
A's agent.py appends user/agent turns to it from session events, E's `end_call` ships it.

Until `server/contracts.py` lands (D), keep a local copy of `CallBrief`/`CallEvent`
in `call_agent/contracts_local.py` with a `TODO(hack): replace with server.contracts`.

## Prompt rules (from CLAUDE.md)

- Opening line. Team decision (Ehsan, 24 Oct): for the hackathon demo we call our own
  teammates, so NO AI disclosure by default. The agent speaks as the caller's assistant
  in first person plural / on behalf of `caller_name`, never claims to be a human,
  and never says it is an AI unless asked directly (then it answers truthfully, briefly).
  DE default: "Hallo, guten Tag, ich rufe fuer {caller_name} an. Er haette gern ..."
  Env flag `DISCLOSE_AI=true` (read in prompts.py, default false) switches to the
  CLAUDE.md variant: "Hallo, guten Tag, ich rufe fuer {caller_name} an, ich bin sein
  digitaler Assistent. Er haette gern ..." Mention the flag in the README.
  Tell s1na/A about this deviation from CLAUDE.md section "Call agent spec".
- The callee speaks first on outbound calls; handle "Ristorante X, guten Tag?".
- One or two short sentences per reply. No lists, no markdown, no em dashes.
- Stick to `goal`. Accept alone only what `allowed_decisions` allows.
  Anything in `must_ask_user_for`, or anything unexpected: call `ask_user`.
- Confirm the final details back once (date, time, persons, name), then `end_call`.
- Language from `brief.language`; `user_facts` gives the booking name, callback number.

## Tools

`ask_user(question: str, options: list[str])`
1. `context.disallow_interruptions()`.
2. Say the hold line: DE "Einen Moment bitte, ich frage kurz nach." / EN "One moment please, let me check."
3. `events.post(CallEvent(type="question", text=question, options=options))`.
4. `answer = await events.wait_answer(40)`; retry once if None (total <= 45 s).
5. Return the answer text to the LLM. On timeout, return a ToolError-ish string that
   tells the LLM to say it will call back and to end the call.

`end_call(outcome: Literal["success","partial","failed"], details: dict[str,str])`
1. Say goodbye (LLM already did, or say a short one).
2. `await ctx.wait_for_playout()`.
3. `events.post(CallEvent(type="result", text=summary, outcome=outcome, details=details, transcript=transcript.as_list()))`.
4. Hang up via `get_job_context().delete_room()` if running inside LiveKit; no-op in the harness.

## Isolation testing (no phone, no server, no LiveKit)

Three terminals:

T1  `claude` (Claude Code) in the repo, working on the files above.
T2  `python scripts/fake_server.py`  -> FastAPI on :8001 that prints every CallEvent
    it receives and answers `GET /calls/{id}/answer` from stdin (you type the user's
    poll answer) or from a `--auto "yes"` flag.
T3  `python scripts/chat_test.py --brief fixtures/brief_italian.json --server http://localhost:8001`
    -> text harness: you type the restaurant's lines, Claude Haiku replies using
    `build_instructions(brief)` and the same two tools (speech lines are printed as
    `[SAY] ...`). Prints time-to-first-token per turn. `--model claude-haiku-4-5`
    to compare models.

Later (14:30+), T4 runs A's `python call_agent/agent.py dev` and E's files are
imported there unchanged.

Fixtures: `fixtures/brief_italian.json` (DE, table for 4, 20:00, allowed_decisions
"time within 30 min of 20:00", must_ask_user_for ["deposit or fee", "different date"]),
`fixtures/brief_doctor.json` (DE, appointment, must ask for any date).

Test script to run manually in T3 (play the restaurant):
1. "Ristorante Roma, guten Tag?"           -> expects greeting + request in 1-2 sentences (no disclosure unless DISCLOSE_AI=true)
2. "Um 20 Uhr ist leider voll, 21 Uhr?"    -> expects ask_user (21:00 is outside 30 min)
   (answer "yes" in T2)                    -> expects acceptance + confirmation
3. "Auf welchen Namen?"                    -> expects booking name from user_facts, no ask_user
4. "Wir brauchen 10 Euro Anzahlung pro Person." -> expects ask_user (must_ask_user_for)
5. "Alles klar, bis morgen!"               -> expects goodbye + end_call with details
6. "Moment, bin ich hier mit einem Computer?" -> expects a short truthful yes, then continue

## Order of work

1. contracts_local.py, fixtures, events.py (with httpx, timeouts, masked logging).
2. prompts.py; chat_test.py with tools stubbed; tune the German on the 5-line script.
3. fake_server.py; real tools.py using EventsClient; run the full script end to end.
4. Measure Haiku 5.5 vs 4.5 TTFT; pick the faster; note it in this file.
5. Hand `make_tools`/`build_instructions` to A; be the restaurant on A's test calls.
