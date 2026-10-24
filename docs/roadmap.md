# Roadmap

Hackathon day: Saturday 24 Oct 2026. Competition opt-in at **19:00**, live demo at **20:00**.

Priority: a demo that works reliably, not a complete product.

## Demo script (the thing that must work)

1. User sends a voice note: "book a table for four tomorrow at eight at the usual Italian place".
2. Agent replies with a poll, "Inside or outside?", and the user taps "Inside". The restaurant comes from memory.
3. Agent texts "Calling now". A teammate's phone rings and they play the restaurant.
4. Teammate offers 21:00 instead of 20:00. The user gets a live poll, "They offer 21:00, ok?", and taps yes. The agent confirms on the call.
5. Summary arrives in iMessage.

## Milestones

| # | Target | Milestone | Done when |
| --- | --- | --- | --- |
| 1 | **14:30 gate** | Telephony and messaging smoke tests | `scripts/dial_test.py` rings a teammate and a hello-world agent talks. `scripts/send_test.py` sends a poll and the vote shows up in server logs |
| 2 | | Orchestrator produces a CallBrief | A text request turns into a correct `CallBrief` with the call mocked |
| 3 | | Call agent completes a booking | The agent completes a booking call from a `CallBrief` and posts a `result` event |
| 4 | **17:00 gate** | Full demo end to end | The demo script runs once, start to finish |
| 5 | | Polish | Mid-call question round trip, voice notes, memory polish, latency tuning |
| 6 | **before 19:00** | Rehearse | Three rehearsals and a recorded backup video |

## Workstreams

| Area | Folder | Key pieces |
| --- | --- | --- |
| Gateway | `server/gateway.py`, `server/main.py` | Linq webhooks, send text and polls, voice-note download, typing indicator |
| Orchestrator | `server/orchestrator.py` | Sonnet tool loop: `ask_user`, `send_message`, `remember`, `place_call`, `find_business` |
| Memory | `server/memory.py` | SQLite users, facts, messages, calls, seeded `businesses` table |
| Calls (server side) | `server/calls.py` | LiveKit dispatch, event intake, `/answer` long-poll |
| Call agent | `call_agent/` | SIP dial, Reson8 STT, Haiku, ElevenLabs TTS, `ask_user` and `end_call` tools, latency logs |
| Voice notes | `server/stt.py` | ElevenLabs Scribe v2 transcription |
| Contracts | `server/contracts.py`, `contracts.md` | `CallBrief`, `CallEvent` (frozen, change only with the whole team) |

## Fallbacks if behind

Drop in this order:

1. Mem0 (SQLite memory is enough).
2. Voice notes (text only).
3. Mid-call polls (the agent accepts the closest time within 30 minutes).

## Out of scope

- Real business search (the demo uses a seeded table of teammate numbers).
- Dialing any number not in `ALLOWED_CALL_NUMBERS`.
