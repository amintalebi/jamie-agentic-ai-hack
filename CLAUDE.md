# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Call-for-me iMessage agent ("Eve")

The assistant is called Eve in everything users see. "Jamie" survives only in the domain (`jamie-agentic-hack.app`), the repo name and internal identifiers (LiveKit agent name `jamie-call-agent`, DB user, Docker images, localStorage keys); do not rename those, it breaks deploys and signs everyone out.

Hackathon project (Agentic AI Hack, Berlin, Sat 24 Oct 2026). Hard deadline: competition opt-in at 19:00, live demo at 20:00. Optimise for a demo that works reliably, not for completeness. When in doubt, pick the simplest thing that works and leave a `TODO(hack)` comment.

## Commands

```
docker compose up --build              # Postgres (pgvector) :5432, server :8000, dashboard PWA :8080/app/
curl localhost:8000/health             # checks the DB connection and reports the pgvector version
docker compose down -v                 # stop and wipe the DB volume (re-runs db/init/*.sql next start)
```

Run the server without Docker, from the repo root (no database needed; every adapter defaults to its fake):
```
pip install -r server/requirements.txt
ALLOWED_CALL_NUMBERS=+4915100001234 uvicorn server.main:app --reload
# fake an inbound iMessage; "/call" places the scripted demo call
curl -XPOST localhost:8000/dev/inbound -H 'content-type: application/json' \
  -d '{"message_id":"m1","user_id":"+4917600005678","text":"/call"}'
# answer the mid-call poll
curl -XPOST localhost:8000/dev/inbound -H 'content-type: application/json' \
  -d '{"message_id":"m2","user_id":"+4917600005678","vote":"Yes"}'
```
Fake sends show up in the server log as `[fake send_text]` / `[fake send_poll]`.

Call agent without a phone: `python scripts/fake_server.py` plus `python scripts/chat_test.py` (see `docs/role-e-call-conversation.md`).

Dashboard PWA (`web/`, Vite + React + OpenUI):
```
cd web && npm install
npm run dev          # http://localhost:5173/app/, proxies /api to the server on :8000
npm test             # parses and renders the server's sample screens
npm run fixtures     # regenerate those samples after changing server/dashboard.py
npm run build        # web/dist (a local `uvicorn` also serves it at :8000/app/ if built)
```
In Docker the PWA is its own image (`web/Dockerfile`: Node build stage, then non-root nginx on 8080 serving precompressed files and proxying `/api/` to `API_URL`). Open `http://localhost:8080/app/`, enter your phone number and the 6-digit code texted to it (`server/auth.py`, Twilio SMS with `SMS_MODE=twilio`; in fake mode the code is in the server log as `[fake send_sms]`). Sending retries Twilio hiccups, and each resend this hour waits longer (30/60/120/300 s, max 5 per hour). The SMS ends with `@APP_DOMAIN #code` so iOS and Android autofill it, and the form submits by itself. The first login signs the user up (`server/users.py`, `users` table) and sends a welcome iMessage via Linq; the user counts as activated only once that send succeeds, so it is retried on the next login. The session token is kept in localStorage for 30 days and is signed with `AUTH_SECRET`.

There are no Python tests or lint config yet.

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

Actual layout differs from the sketch above in two ways:
- Contracts live in `common/contracts.py` (not `server/`), shared by both processes. `call_agent/contracts_local.py` is a shim that re-exports them.
- `server/` is a package: run it as `server.main:app` from the repo root and import siblings as `from server import calls`. The server Docker build context is the repo root, so the image includes `common/`.

### Dashboard (call monitoring)
`server/history.py` records every call and its timeline (Postgres tables `calls` and `call_events`, created at startup; in memory without `DATABASE_URL`). Writes are queued in the background (in order per call, 10 s timeout) and never raise, so a slow or broken database cannot delay or break a live call. Calls still live at startup are closed as lost, and a live call far past its `max_call_seconds` shows as "lost". `server/dashboard.py` turns that history into screens written in OpenUI Lang (https://openui.com), built in code with no LLM, and serves them at `/api/dashboard/*` for the phone number in the bearer token from `/api/auth/verify`. `web/` registers the matching components (`web/src/library.tsx`) and renders them with OpenUI's `<Renderer>`, refreshing every 2 s while a call is live.

Styling is Tailwind v4 plus shadcn/ui (`web/components.json`; primitives in `web/src/components/ui/`, add more with `cd web && npx shadcn@4.22.1 add <name>`). Theme tokens, brand gradient and animations live in `web/src/index.css`; dark mode follows the OS. Brand pieces (logo, aurora, waveform, confetti) are in `web/src/components/brand.tsx`, and the animated iMessage demo on the sign-in screen is `web/src/components/phone-demo.tsx`.

OpenUI Lang arguments are **positional, in Zod key order**. When you change a component, change the Python call in `dashboard.py` and the schema in `library.tsx` together, then run `npm run fixtures && npm test`. Props the server may send as `null` must be `.optional()` (OpenUI rejects `null` for required props, even `.nullable()` ones).

### Adapters and fakes
Every outside service sits behind a small `Protocol` with a real and a fake implementation, chosen by an env var (all default to `fake`): `gateway.Messenger` (`MESSENGER_MODE=fake|linq`), `stt.Transcriber` (`STT_MODE=fake|scribe`), `calls.CallDispatcher` (`CALLS_MODE=fake|livekit`), `memory.MemoryStore` (in-memory for now). The fake dispatcher plays a scripted call over HTTP against the server's own `/calls/...` routes, so the mid-call poll round trip works without telephony. Module-level functions (`gateway.send_text`, `memory.remember`, ...) are the interface other modules call; never reach into an adapter directly.

Modules import each other as modules (`from server import calls`) and look names up at call time; this is what lets `gateway`, `calls` and `orchestrator` import each other without cycles breaking.

## Interfaces between modules

Owners agreed on these signatures (see `docs/team-split.md`); stub them until the real code lands rather than inventing new ones.

| Caller -> callee | Interface |
| --- | --- |
| `call_agent/agent.py` -> prompts/tools | `prompts.build_instructions(brief: CallBrief) -> str`, `tools.make_tools(brief, ...) -> list` |
| gateway -> orchestrator | `async orchestrator.handle_message(user_id: str, text: str) -> None` |
| gateway -> calls | `calls.match_vote(user_id, option, poll_id) -> call_id or None`, `calls.resolve_answer(call_id, answer: str) -> None` |
| orchestrator, calls -> gateway | `async gateway.send_text(to, text)`, `async gateway.send_poll(to, question, options) -> poll_id or None` (votes carry it back as `InboundMessage.poll_id`) |
| orchestrator -> calls | `async calls.place_call(brief: CallBrief) -> call_id` |
| calls -> orchestrator | `async orchestrator.handle_call_result(event: CallEvent) -> None` |
| orchestrator -> calls | `calls.get_brief(call_id) -> CallBrief or None`, `calls.active_calls(user_id) -> list[CallBrief]`, `calls.CallRefused` |
| gateway -> orchestrator/calls | `gateway.route_inbound(InboundMessage)`: dedupe, transcribe, route votes |
| orchestrator -> memory | `get_facts`, `remember`, `log_message`, `recent_messages`, `save_call`, `find_business` (all async) |
| `call_agent/events.py` -> server (HTTP) | `POST /calls/{id}/events` with a `CallEvent`; `GET /calls/{id}/answer?wait=40` |

`user_id` everywhere is the user's E.164 phone number, so `gateway.send_text(to=user_id, ...)` needs no lookup.

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
- `place_call(brief: CallBrief fields minus call_id)`: with `SAFE_MODE` on, refuses numbers not in `ALLOWED_CALL_NUMBERS`; otherwise dispatches the call and returns the call_id.
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
- Keep it boring: FastAPI, Pydantic, httpx, sqlite3, the official SDKs. No new frameworks. Exception, agreed for the demo: the PWA (`web/`) uses Tailwind v4 and shadcn/ui on top of React and OpenUI; keep new UI on those rather than adding more.
- Every external call gets a timeout and a log line. No silent failures: on errors, text the user something sensible.
- Never log full API keys or full phone numbers (mask all but the last 4 digits).
- Dialling goes through `common/safe_mode.py`. `SAFE_MODE` (default on, set it on both the server and the call agent) only allows `ALLOWED_CALL_NUMBERS`. With it off, any valid E.164 number may be called (real restaurants from `search_restaurants`, confirmed by the user first) and the call agent always discloses that it is an AI assistant.
- No em dashes in any text the agent sends to users.
- Small commits, one component per person; do not edit another owner's folder without asking.
