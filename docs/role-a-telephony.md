# Role A: Telephony and audio pipeline

Owner: Arman (ArmanShirzad). Branch: `feat/call-agent`.
Owns: `call_agent/agent.py`, `scripts/dial_test.py`, `scripts/setup_trunks.py`.
Role E (Ehsan) owns `prompts.py`, `tools.py`, `events.py`; `agent.py` imports them unchanged.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r call_agent/requirements.txt
cp .env.example .env   # fill in the keys listed in call_agent/agent.py
.venv/bin/python call_agent/agent.py download-files
```

Keys the worker needs: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`,
`RESON8_API_KEY`, `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID_DE`, `ALLOWED_CALL_NUMBERS`,
`SERVER_PUBLIC_URL` (defaults to the fake server on :8001).

## One-off: trunks (needs Twilio keys and a Twilio number)

```bash
.venv/bin/python scripts/setup_trunks.py
```

Creates the Twilio Elastic SIP trunk `jamie.pstn.twilio.com`, a credential list for termination auth, attaches the
number, then creates the LiveKit outbound trunk and prints `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` for `.env`.
Safe to re-run. If Twilio was configured by hand in the console, run with `--livekit-only`.

## Milestone 1: dial test (14:30 gate)

Terminal 1, the worker (stays up, takes dispatches):
```bash
.venv/bin/python call_agent/agent.py dev
```

Terminal 2, optional, the fake server so the result event has somewhere to go:
```bash
.venv/bin/python scripts/fake_server.py --auto Yes
```

Terminal 3, ring a teammate:
```bash
.venv/bin/python scripts/dial_test.py --to +49151XXXXXXX                 # hello-world call in German
.venv/bin/python scripts/dial_test.py --to +49151XXXXXXX --brief fixtures/brief_italian.json   # real booking brief
```

The worker log shows the dial, "answered after N s", each turn with its latency line, tool calls and the hangup.

## How a call runs (agent.py)

1. Dispatch arrives with the `CallBrief` JSON as metadata (agent name `jamie-call-agent`).
2. Number must be in `ALLOWED_CALL_NUMBERS`, else a `failed` event and no call.
3. Session starts first (ElevenLabs Scribe realtime STT with VAD turn detection, Claude Haiku, ElevenLabs TTS),
   then the SIP participant is created with `wait_until_answered`. Rejected, busy or no answer: `failed` event.
4. `started` event once the callee is in the room. The agent says the opening line as soon as the callee pauses,
   after `CALL_SILENCE_OPEN_S` of silence, or after 6 s at the latest. It never waits for the STT to close a turn.
5. Every turn is logged on one line: `stt_final`, `end_of_turn`, `llm_ttft`, `tts_ttfb`, `e2e`.
6. `end_call` (role E) posts the `result` and deletes the room. If the business hangs up first, `agent.py`
   posts a `partial` result with the transcript. 25 s before `max_call_seconds` the agent is told to wrap up;
   at the limit it is cut off with a `partial` result.

## Knobs

| Env | Default | Effect |
| --- | --- | --- |
| `CALL_AMD` | 0 | Answering machine detection before speaking. Adds start-of-call latency; on for rehearsals only if voicemail is a risk |
| `CALL_PREEMPTIVE` | 1 | Start the LLM on Reson8's eager transcript |
| `CALL_DIAL_TIMEOUT_S` | 45 | Ring window |
| `CALL_SILENCE_OPEN_S` | 3 | Open the conversation if the callee stays silent after pickup |
| `ELEVENLABS_TTS_MODEL` | eleven_flash_v2_5 | TTS model |
| `CALL_LLM_MODEL` | claude-haiku-4-5 | See `config.py`. Haiku 5.5 works too: `agent.py` turns its default thinking off (the plugin cannot carry thinking blocks across tool calls) |
| `CALL_STT` | elevenlabs | Scribe v2 realtime with VAD turn detection. `reson8` restores Reson8 with its server-side turn detection |
| `CALL_AGENT_NAME` | jamie-call-agent | Must match the server's `.env` |

## Measured 24 Oct, 13:50 to 14:00, on the hackathon wifi

| What | Result |
| --- | --- |
| Reson8 streaming, 9 s German sample | eager end-of-turn transcript 0.37 to 0.44 s after end of speech; final 0.98 to 1.44 s; transcript exact |
| Reson8 prerecorded, same sample | 0.9 to 3.2 s per request |
| Haiku 4.5 time to first token, 4 runs each, via the Anthropic SDK and via the LiveKit plugin | 0.62 to 2.31 s, medians 0.78 to 1.47 s |
| Haiku 5.5 with thinking off, same | 0.58 to 2.62 s, medians 1.22 to 1.49 s |
| Haiku 5.5 default (thinking on), max_tokens 80 | spends the whole budget thinking, no text at all |

The network was noisy (single runs varied 3x), so the two Haikus are within noise. 4.5 stays the default. The main lever
is `CALL_PREEMPTIVE=1`, which starts the LLM on Reson8's eager transcript about 0.4 s after the speaker stops.

## Console mode (no phone, tests STT, LLM and TTS on your laptop)

```bash
CALL_BRIEF_FILE=fixtures/brief_italian.json .venv/bin/python call_agent/agent.py console
```

## Real calls, 24 Oct 14:15 to 14:31 (four dial tests to a teammate's mobile)

| Call | What happened |
| --- | --- |
| 1 | Dial, ring, answer after 9.8 s, STT heard the callee, started and result events posted. Agent silent: every LLM request got a 400 because strict tool schemas reject `end_call(details: dict)`. Fixed with non-strict schemas. |
| 2 | First job process segfaulted in `liblivekit_ffi` as the SIP audio started; LiveKit re-dispatched and the retry answered after 6.6 s. Agent silent for 8 s because it waited for Reson8 to close the callee's turn. Callee hung up. |
| 3 | Agent opened 2.3 s after pickup (LLM first token 0.64 s, TTS first audio 0.18 s). Callee answered, but Reson8 closed that turn only after 16 s of short pauses, so the agent never replied. The American voice reading German was hard to understand. |
| 4 | ElevenLabs Scribe realtime plus VAD: opened 2.1 s after pickup, callee's turn closed 0.75 s after end of speech, reply end to end 1.84 s, `end_call` posted success. |

Open items: a German library voice on the ElevenLabs account (the API key cannot add voices); one segfault in
`liblivekit_ffi.dylib` (livekit-rtc 1.1.21) seen once in four calls, LiveKit retried the job automatically.
