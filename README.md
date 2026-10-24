# Jamie: call-for-me iMessage agent

A personal assistant you text on iMessage. Tell it what you need ("book a table for four tomorrow at eight at the usual Italian place") and it phones the business for you, in German or English, then texts you the result.

Built at the Agentic AI Hack, Berlin, 24 Oct 2026.

## What it does

1. Understands text messages and voice notes.
2. Asks clarifying questions as native iMessage polls.
3. Remembers you: your name, your usual places, your preferences.
4. Calls the business and discloses up front that it is an AI assistant.
5. If the business offers something outside the brief (for example 21:00 instead of 20:00), puts them on hold, sends you a live poll and relays your answer.
6. Texts you a short summary when the call ends.

## Architecture

```
iPhone <-> Linq iMessage API <-> server/ (FastAPI)
                                   |- gateway: Linq webhooks, send text/poll, voice-note download
                                   |- orchestrator: Claude Sonnet 5.5 tool loop
                                   |- memory: SQLite user profiles + message log
                                   |- calls: dispatch via LiveKit, receive call events
                                        |
                                        v  (LiveKit agent dispatch, brief in metadata)
                               call_agent/ (LiveKit Agents worker)
                                   Reson8 STT -> Claude Haiku -> ElevenLabs TTS
                                        |
                                        v
                               Twilio Elastic SIP trunk -> business phone
```

Two Python 3.11+ processes:

- `server/` receives iMessages, runs the orchestrator, stores memory and dispatches calls.
- `call_agent/` is the LiveKit worker that holds the actual phone conversation.

They share `CallBrief` and `CallEvent` from `contracts.py`. See [CLAUDE.md](CLAUDE.md) for the full spec.

## Stack

| Layer | Choice |
| --- | --- |
| iMessage | Linq API v3 |
| Orchestrator LLM | Claude Sonnet 5.5 |
| Call LLM | Claude Haiku 5.5 (fallback Haiku 4.5) |
| Voice-note STT | ElevenLabs Scribe v2 |
| Call STT | Reson8 |
| TTS | ElevenLabs |
| Telephony | Twilio SIP trunk into LiveKit Cloud |
| Memory | SQLite |

## Setup

Run the server and Postgres (with pgvector) locally:

```
docker compose up --build
curl localhost:8000/health
```

The call monitor (an installable PWA) is at `http://localhost:8080/app/#token=<DASHBOARD_TOKEN>`. It shows the call history, the live state of each call, any question waiting on you, the timeline and the transcript.

Full setup, as components land:

1. Copy `.env.example` to `.env` and fill in the keys.
2. Set `ALLOWED_CALL_NUMBERS` to the numbers you are allowed to dial. The agent refuses every other number.
3. Expose the server with ngrok and put the URL in `SERVER_PUBLIC_URL`.
4. Start `server/` and `call_agent/`.

By default the call agent does not announce itself as an AI in its opening line (the demo calls our own teammates). It never claims to be human and says so truthfully if asked. Set `DISCLOSE_AI=true` to make it introduce itself as a digital assistant up front.

## Docs

- [Roadmap](docs/roadmap.md)
- [Team split](docs/team-split.md)
- [Project spec and conventions](CLAUDE.md)
