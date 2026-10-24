# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Call-for-me iMessage agent ("Jamie")

Hackathon project (Agentic AI Hack, Berlin, Sat 24 Oct 2026). Hard deadline: competition opt-in at 19:00, live demo at 20:00. Optimise for a demo that works reliably, not for completeness. When in doubt, pick the simplest thing that works and leave a `TODO(hack)` comment.

## Commands

```
docker compose up --build              # Postgres (pgvector) on :5432 + server on :8000
curl localhost:8000/health             # checks the DB connection and reports the pgvector version
docker compose down -v                 # stop and wipe the DB volume (re-runs db/init/*.sql next start)
```

Run the server without Docker (needs a Postgres with pgvector reachable at `DATABASE_URL`):
```
cd server && pip install -r requirements.txt && DATABASE_URL=postgresql://jamie:jamie@localhost:5432/jamie uvicorn main:app --reload
```

There are no tests or lint config yet.

## What we are building

A personal assistant you text on iMessage (text or voice notes). It:
1. Understands the request and asks clarifying questions as native iMessage polls (multiple choice).
2. Remembers the user (name, usual places, preferences).
3. Phones businesses on the user's behalf (for example: book a restaurant table in German).
4. During the call, if the business offers something outside the brief, puts the business on hold ("one moment, let me check"), sends the user a live poll, and relays the answer.
5. Texts the user a summary when the call ends.

### Demo script (the thing that must work)
1. User sends a voice note: "book a table for four tomorrow at eight at the usual Italian place".
2. Agent replies with a poll: "Inside or outside?" User taps "Inside". The restaurant comes from memory.
3. Agent texts "Calling now". A teammate's phone rings and plays the "restaurant".
4. Teammate offers 21:00 instead of 20:00. User gets a live poll "They offer 21:00, ok?" and taps yes. Agent confirms on the call.
5. Summary arrives in iMessage.

## Architecture

```
iPhone <-> Linq iMessage API <-> server/ (FastAPI)
                                   |- gateway: Linq webhooks, send text/poll, voice-note download
                                   |- orchestrator: Claude Sonnet 5.5 tool loop
                                   |- memory: SQLite user profiles + message log
                                   |- calls: dispatch via LiveKit, receive call events
                                        |
                                        v  (LiveKit agent dispatch, brief in metadata)
                               call_agent/ (LiveKit Agents worker, Python)
                                   Reson8 STT -> Claude Haiku -> ElevenLabs TTS
                                        |
                                        v
                               Twilio Elastic SIP trunk -> business phone
```

Two processes only: `server` and `call_agent`. Both Python 3.11+.

## Repo layout

```
/server
  main.py            FastAPI app, routes
  gateway.py         Linq client: send_text, send_poll, download_attachment, typing indicator
  orchestrator.py    Claude tool loop, system prompt, tool handlers
  memory.py          SQLite: users, facts, messages, calls
  calls.py           place_call (LiveKit dispatch + SIP), pending-question store
  stt.py             voice notes -> text (ElevenLabs Scribe v2)
  contracts.py       Pydantic models for CallBrief and CallEvent (single source of truth)
/call_agent
  agent.py           LiveKit worker entrypoint
  prompts.py         call-agent system prompt built from CallBrief
  tools.py           ask_user / end_call tools for the call LLM
  events.py          HTTP client posting CallEvents back to server
/scripts
  dial_test.py       dial a number with a hello-world agent (telephony smoke test)
  send_test.py       send a text + poll to a number via Linq (gateway smoke test)
contracts.md         human-readable copy of the contracts
.env.example
```

`contracts.py` lives in `server/`. Import it from both processes (shared via a small `common/` package or path hack; whichever is fastest).

## Interfaces between modules

Owners agreed on these signatures (see `docs/team-split.md`); stub them until the real code lands rather than inventing new ones.

| Caller -> callee | Interface |
| --- | --- |
| `call_agent/agent.py` -> prompts/tools | `prompts.build_instructions(brief: CallBrief) -> str`, `tools.make_tools(brief, ...) -> list` |
| gateway -> orchestrator | `async orchestrator.handle_message(user_id: str, text: str) -> None` |
| gateway -> calls | `calls.pending_question(user_id) -> call_id or None`, `calls.resolve_answer(call_id, answer: str) -> None` |
| orchestrator, calls -> gateway | `async gateway.send_text(to, text)`, `async gateway.send_poll(to, question, options)` |
| orchestrator -> calls | `async calls.place_call(brief: CallBrief) -> call_id` |
| calls -> orchestrator | `async orchestrator.handle_call_result(event: CallEvent) -> None` |
| `call_agent/events.py` -> server (HTTP) | `POST /calls/{id}/events` with a `CallEvent`; `GET /calls/{id}/answer?wait=40` |

File ownership per person is in `docs/team-split.md`; check it before editing a file you do not own.

## Stack and models

| Layer | Choice | Notes |
| --- | --- | --- |
| iMessage | Linq API v3 | Supports native iMessage polls and tapback events. Docs: https://docs.linqapp.com |
| Orchestrator LLM | `claude-sonnet-5-5` | Anthropic Python SDK, tool use, streaming not required |
| Call LLM | `claude-haiku-5-5` at lowest effort / thinking off; fallback `claude-haiku-4-5` | Time to first token must stay under ~1 s. Measure both, keep the faster |
| Voice-note STT | ElevenLabs Scribe v2 (speech-to-text API) | Multilingual, handles German/English/Persian mixing. Verify the exact model id in ElevenLabs docs |
| Call STT | Reson8 via `livekit-plugins-reson8` | Docs: https://docs.reson8.dev/llms-full.txt (read this, it is LLM-friendly). Set `language="de"` for German calls |
| TTS | ElevenLabs via `livekit-plugins-elevenlabs`, v4 Turbo or Flash v2.5 | Native German voice id in `.env` |
| Telephony | Twilio number + Elastic SIP trunk into LiveKit Cloud | LiveKit outbound docs: https://docs.livekit.io/telephony/making-calls.md |
| Memory | SQLite (stdlib `sqlite3`) | Mem0 is optional, only if time allows |

Always check the current docs of Linq, Reson8 and LiveKit before writing client code; do not guess endpoint shapes. If an API detail is unclear, write a thin wrapper with a clear TODO and keep moving.

## Environment variables (`.env.example`)

```
ANTHROPIC_API_KEY=
LINQ_API_KEY=
LINQ_FROM_NUMBER=
LINQ_WEBHOOK_SECRET=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID_DE=
RESON8_API_KEY=
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_NUMBER=
SERVER_PUBLIC_URL=        # ngrok URL, used for Linq webhooks and call events
ALLOWED_CALL_NUMBERS=     # comma-separated E.164 list; calls to anything else are refused
```

## Contracts (do not change without telling the whole team)

```python
class CallBrief(BaseModel):
    call_id: str
    user_id: str
    to_number: str                 # E.164
    language: Literal["de", "en"]
    caller_name: str               # who we call on behalf of, e.g. "Sina"
    goal: str                      # "Book a table for 4, Sat 25 Oct, 20:00, inside"
    user_facts: dict[str, str]     # name, callback number, booking name, etc.
    allowed_decisions: list[str]   # things the agent may accept without asking, e.g. "time within 30 min of 20:00"
    must_ask_user_for: list[str]   # always ask, e.g. "any deposit or fee", "different date"
    max_call_seconds: int = 240

class CallEvent(BaseModel):
    call_id: str
    type: Literal["started", "question", "progress", "result", "failed"]
    text: str                      # human-readable line
    options: list[str] | None = None        # for type="question"
    outcome: Literal["success", "partial", "failed"] | None = None  # for type="result"
    details: dict[str, str] | None = None   # e.g. {"time": "21:00", "name": "Sina"}
    transcript: list[dict] | None = None    # [{"speaker": "agent"|"business", "text": "..."}]
```

## Server spec

### Routes
- `POST /webhooks/linq`: inbound messages, poll votes, tapbacks. Verify the signature if Linq provides one. Return 200 fast and process in a background task.
- `POST /calls/{call_id}/events`: CallEvents from the call agent.
- `GET /calls/{call_id}/answer?wait=40`: long-poll used by the call agent to get the user's answer to its last question. Returns `{"answer": "..."}` or `{"answer": null}` on timeout.
- `GET /health`.

### Inbound message flow
1. Dedupe by message id.
2. If the message has an audio attachment, download it and transcribe with Scribe v2; treat the transcript as the user's text.
3. If it is a poll vote or tapback answering a pending question, resolve that question (see below) instead of starting a new orchestrator turn, unless no question is pending.
4. Otherwise append to the message log and run an orchestrator turn. Show a typing indicator while it runs.

### Orchestrator (Claude Sonnet 5.5)
System prompt essentials:
- You are a personal assistant that lives in iMessage. Replies are short, friendly, texting style, no markdown.
- Before placing a call, make sure you have everything the call needs. Ask at most one question at a time, and prefer `ask_user` with 2 to 4 options over open questions.
- Use what memory says instead of asking again. Save durable facts the user states with `remember`.
- Never place a call without stating in one line what you are about to do.

Inject on every turn: the user's profile facts, the last ~20 messages, and any active calls.

Tools:
- `ask_user(question: str, options: list[str])`: sends a Linq poll (fallback: numbered text options). Ends the turn; the vote arrives later as a new event.
- `send_message(text: str)`.
- `remember(key: str, value: str)`: upsert a user fact.
- `place_call(brief: CallBrief fields minus call_id)`: refuses numbers not in `ALLOWED_CALL_NUMBERS`; otherwise dispatches the call and returns the call_id.
- `find_business(query: str)`: for the demo, look up in a small `businesses` table seeded with the teammate "restaurant" numbers. Real search is out of scope.

### Mid-call questions
When a CallEvent of type `question` arrives:
1. Store it as the pending question for that user, keyed by call_id.
2. Send the user a poll with the event's options (prefix the question with "On the call:").
3. When the vote arrives, store the answer so `GET /calls/{call_id}/answer` returns it.

When a `result` arrives: send the user a 2 to 3 line summary written by Sonnet from the result details, save the call record, and `remember` anything durable (for example the restaurant's number).

## Call agent spec (LiveKit Agents worker)

- Worker receives a job via agent dispatch; the CallBrief JSON is in job metadata.
- Create the SIP participant for `to_number` on `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`, wait for answer. Use LiveKit answering-machine detection; on voicemail, leave no message and emit `failed`.
- Pipeline: Reson8 STT (language from brief) -> Claude Haiku -> ElevenLabs TTS. Stream everything. Allow interruptions.
- First line, always: disclose it is an AI assistant calling on behalf of `caller_name`. In German: "Hallo, hier ist der KI-Assistent von {caller_name}. Ich rufe an, um ..."
- Prompt rules for Haiku: replies of one or two short sentences; stick to the goal; accept only what `allowed_decisions` permits; for anything in `must_ask_user_for` or anything unexpected, call the `ask_user` tool.
- `ask_user` tool in the call agent: say "Einen Moment bitte, ich frage kurz nach." (or English equivalent), POST a `question` event, long-poll `/answer` for up to 45 s, then continue with the answer. On timeout, say you will call back and end politely.
- `end_call(outcome, details)` tool: say goodbye, POST a `result` event with the transcript, hang up.
- Hard stop at `max_call_seconds`.
- Log per-turn latency: end of business speech, STT final, LLM first token, TTS first audio. Print a one-line summary per turn.

## Milestones (build in this order)

1. **14:30 gate:** `scripts/dial_test.py` rings a teammate and a hello-world agent talks. `scripts/send_test.py` sends a poll and the vote shows up in server logs.
2. Orchestrator turns a text request into a correct CallBrief (test with the call mocked).
3. Call agent completes a booking call from a CallBrief and posts a result.
4. **17:00 gate:** full demo flow once, end to end.
5. Mid-call question round trip, voice notes, memory polish, latency tuning.
6. Rehearse three times, record a backup video before 19:00.

Fallbacks if behind: drop Mem0; drop voice notes (text only); drop mid-call polls (agent accepts the closest time within 30 min).

## Conventions
- Keep it boring: FastAPI, Pydantic, httpx, sqlite3, the official SDKs. No new frameworks.
- Every external call gets a timeout and a log line. No silent failures: on errors, text the user something sensible.
- Never log full API keys or full phone numbers (mask all but the last 4 digits).
- Only ever dial numbers in `ALLOWED_CALL_NUMBERS`.
- No em dashes in any text the agent sends to users.
- Small commits, one component per person; do not edit another owner's folder without asking.
