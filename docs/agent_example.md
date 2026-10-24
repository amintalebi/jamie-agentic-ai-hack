# Reference call worker (`docs/agent_example.py`)

A complete LiveKit Agents worker that wires role E's modules into a phone call. It is a reference for `call_agent/agent.py` (owned by A); copy what you need. API names were checked against what is installed in `.venv`: livekit-agents 1.8.9, livekit-api 1.2.2, livekit-plugins-reson8 0.2.4, and the elevenlabs and anthropic plugins 1.8.9.

## Run it

```bash
uv pip install --python .venv/bin/python -r call_agent/requirements.txt
.venv/bin/python docs/agent_example.py dev          # registers as agent "jamie-caller"

# place a test call (lk CLI); the CallBrief JSON is the job metadata
lk dispatch create --new-room --agent-name jamie-caller --metadata "$(cat fixtures/brief_italian.json)"
```

For a dry run without a server, start `scripts/fake_server.py` and set `SERVER_PUBLIC_URL=http://localhost:8001`.

## Environment (`.env` at the repo root)

| Variable | Used for |
| --- | --- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | worker registration and the SIP API (read by LiveKit itself) |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` | the Twilio outbound trunk |
| `ALLOWED_CALL_NUMBERS` | comma-separated E.164 list; the worker refuses anything else and posts `failed` |
| `SERVER_PUBLIC_URL` | where CallEvents are posted and answers are long-polled |
| `ANTHROPIC_API_KEY` | call LLM and poll translation |
| `CALL_LLM_MODEL` | optional, default `claude-haiku-4-5` |
| `RESON8_API_KEY` | STT |
| `ELEVENLABS_API_KEY` | TTS. Passed explicitly, because the plugin's own default is `ELEVEN_API_KEY` |
| `ELEVENLABS_VOICE_ID_DE`, `ELEVENLABS_VOICE_ID_EN` | voice per `brief.language`. `_EN` is new and only needed for English briefs |
| `DISCLOSE_AI` | optional, see README |

## What it does

1. Reads the `CallBrief` from `ctx.job.metadata` and checks `to_number` against `ALLOWED_CALL_NUMBERS`.
2. Builds the session:
   - `AgentSession(stt=reson8.STT(language=...), llm=anthropic.LLM(model=call_llm_model()), tts=elevenlabs.TTS(...))`
   - `turn_handling={"turn_detection": "stt", "interruption": {"enabled": True}}`: Reson8 detects end of turn server-side. In 1.8 `allow_interruptions=` is deprecated in favour of `turn_handling`.
3. Creates `CallAgent(Agent)` with `build_instructions(brief)` and `make_tools(brief, events, transcript)`.
4. Starts the session, then dials with `CreateSIPParticipantRequest(..., wait_until_answered=True)`.
   - `api.SipCallError` (busy, declined, no answer) posts `failed` with the SIP status and shuts down.
   - Other `api.ServerError`s (auth, bad trunk) do the same.
5. After the callee answers it posts `started`.
6. The callee speaks first. On that first turn, `on_user_turn_completed` adds a one-turn system hint to open with `opening_line(brief)`. If the callee stays silent for 5 s, the agent greets first.
7. `conversation_item_added` feeds `transcript.add("business"|"agent", text)` and logs one latency line per agent turn from `ChatMessage.metrics` (end of turn, STT, LLM time to first token, TTS time to first byte, end to end). `metrics_collected` is deprecated in 1.8.
8. At `max_call_seconds` it asks the LLM to summarise, say goodbye and call `end_call` with `partial`. After 20 s more it posts `result`/`partial` itself and deletes the room.
9. On shutdown it posts a `partial` result with the transcript if the business hung up before `end_call`.

Not done (TODO in code): voicemail detection with `livekit.agents.AMD`, and noise cancellation.
