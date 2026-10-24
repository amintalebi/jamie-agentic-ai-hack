# Team split

This is a proposal for five people. Put your name next to a role and edit freely.

The split follows the seams in the architecture, so each person owns their own files and the only shared surface is `contracts.py` plus the interfaces listed below. The call agent is the riskiest part, so it gets two people: one on the telephony and audio plumbing, one on what the agent says.

## Roles

### A. Telephony and audio pipeline (highest risk, start first)

**Owner:** _name_

**Owns:** `call_agent/agent.py`, `scripts/dial_test.py`

- Set up the Twilio number, the Elastic SIP trunk into LiveKit Cloud and the outbound trunk id.
- `dial_test.py`: ring a teammate with a hello-world agent (**14:30 gate**).
- LiveKit worker entrypoint: read the `CallBrief` from job metadata, create the SIP participant, wait for answer, detect voicemail.
- Audio pipeline: Reson8 STT (language from the brief), then the LLM, then ElevenLabs German TTS, with interruptions on.
- Hard stop at `max_call_seconds`, hang up cleanly.
- Per-turn latency logging (end of speech, STT final, LLM first token, TTS first audio).

### E. Call conversation

**Owner:** _name_

**Owns:** `call_agent/prompts.py`, `call_agent/events.py`, `call_agent/tools.py`

- Call-agent system prompt built from the `CallBrief`: AI disclosure on the first line, one or two short sentences per reply, stick to the goal, respect `allowed_decisions` and `must_ask_user_for`.
- `ask_user` tool: say "Einen Moment bitte, ich frage kurz nach.", POST a `question` event, long-poll `/answer` for up to 45 s, then continue (or promise a call back on timeout).
- `end_call` tool: say goodbye, POST a `result` event with the transcript.
- `events.py`: HTTP client posting `CallEvent`s to the server.
- **Before telephony works:** build a text-only harness (type the restaurant's lines in a terminal) to tune the German prompt without a phone. Measure Haiku 5.5 against Haiku 4.5 time to first token and keep the faster one.
- Plays the restaurant when testing calls with A.

A and E agree early on one hook: `agent.py` calls `prompts.build_instructions(brief)` and registers `tools.make_tools(brief, ...)`.

### B. iMessage gateway and server shell

**Owner:** _name_

**Owns:** `server/main.py`, `server/gateway.py`, `server/stt.py`, `scripts/send_test.py`

- Linq account, sending number, webhook and ngrok.
- `send_test.py`: send a text and a poll, and see the vote in the server logs (**14:30 gate**).
- FastAPI app: `POST /webhooks/linq` with signature check, dedupe and a background task, plus `GET /health`.
- `gateway.py`: `send_text`, `send_poll` (with a numbered-text fallback), `download_attachment`, typing indicator.
- Route each inbound message: a poll vote answering a pending question goes to `calls.resolve_answer`, anything else goes to `orchestrator.handle_message`.
- `stt.py`: voice notes transcribed with ElevenLabs Scribe v2.

### C. Orchestrator and memory

**Owner:** _name_

**Owns:** `server/orchestrator.py`, `server/memory.py`

- SQLite schema for users, facts, messages, calls and businesses. Seed the demo user (Sina), "the usual Italian place" and the teammate restaurant numbers.
- Sonnet 5.5 tool loop with a system prompt and the tools `ask_user`, `send_message`, `remember`, `place_call` and `find_business`.
- Inject profile facts, the last 20 messages and active calls on every turn.
- Milestone 2: a text request becomes a correct `CallBrief`, tested with `place_call` mocked.
- Write the call summary from a `result` event and `remember` durable facts from it.

### D. Contracts, call glue and demo lead

**Owner:** _name_

**Owns:** `server/contracts.py`, `contracts.md`, `server/calls.py`, `.env.example`, the demo run

- **In the first 30 minutes:** commit `contracts.py` and `.env.example` so everyone builds against them.
- `calls.py`: `place_call` (with the `ALLOWED_CALL_NUMBERS` check and LiveKit agent dispatch with the brief in metadata), `POST /calls/{id}/events`, the pending-question store and the `GET /calls/{id}/answer` long-poll.
- Mid-call question flow: a `question` event becomes an "On the call:" poll, and the vote becomes an answer.
- Run integration and drive the **17:00 gate**.
- Owns the three rehearsals and the backup video before 19:00.

## Interfaces between owners

Agree on these signatures early and stub them until the real code lands.

| From | To | Interface |
| --- | --- | --- |
| A (agent.py) | E (prompts, tools) | `prompts.build_instructions(brief: CallBrief) -> str`, `tools.make_tools(brief, ...) -> list` |
| B (gateway) | C (orchestrator) | `async orchestrator.handle_message(user_id: str, text: str) -> None` |
| B (gateway) | D (calls) | `calls.pending_question(user_id) -> call_id or None`, `calls.resolve_answer(call_id, answer: str) -> None` |
| C, D | B (gateway) | `async gateway.send_text(to, text)`, `async gateway.send_poll(to, question, options)` |
| C (orchestrator) | D (calls) | `async calls.place_call(brief: CallBrief) -> call_id` |
| D (calls) | C (orchestrator) | `async orchestrator.handle_call_result(event: CallEvent) -> None` |
| E (events.py) | D (calls) | HTTP: `POST /calls/{id}/events` with a `CallEvent`, `GET /calls/{id}/answer?wait=40` |
| everyone | D | `contracts.py`: `CallBrief`, `CallEvent` |

## Timeline per person

| Time | A | E | B | C | D |
| --- | --- | --- | --- | --- | --- |
| until 14:30 | Trunk setup, `dial_test.py` | Text-only harness, German prompt, Haiku latency check | Linq setup, `send_test.py` | Schema, seed data, prompt draft | Contracts, `.env.example`, help A with accounts |
| 14:30 to 17:00 | Full audio pipeline, plug in E's prompt and tools | `ask_user` and `end_call`, events posting | Webhook routing, voice notes | Tool loop to `CallBrief` (milestone 2) | `calls.py`, events, wire C to A |
| 17:00 gate | everyone: full demo end to end | | | | |
| 17:00 to 19:00 | Latency tuning | Prompt polish from real call transcripts | Poll polish, error texts | Summary, memory polish | Mid-call poll round trip, rehearsals, backup video |

## If someone drops out

- Lose E: A takes the call prompts and tools. Skip the text-only harness.
- Lose D: `contracts.py`, `.env.example` and `calls.py` go to C. Demo lead and backup video go to B.

## Rules (from CLAUDE.md)

- Do not edit another owner's folder or files without asking. `call_agent/` is shared between A and E by file.
- Small commits, one PR per component.
- Change `contracts.py` only after telling the whole team.
