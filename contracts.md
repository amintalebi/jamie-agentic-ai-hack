# Contracts

Source of truth: [`common/contracts.py`](common/contracts.py), imported by `server/` and `call_agent/`. Change it only after telling the whole team.

## CallBrief: server -> call agent

Sent as LiveKit agent-dispatch metadata (JSON) when a call is placed.

| Field | Type | Meaning |
| --- | --- | --- |
| `call_id` | str | Also the LiveKit room name. `calls.place_call` fills it in when passed `""` |
| `user_id` | str | The user's E.164 phone number |
| `to_number` | str | Business number, E.164. Must be in `ALLOWED_CALL_NUMBERS` |
| `language` | `"de"` or `"en"` | Language of the call |
| `caller_name` | str | Who we call for, e.g. "Sina" |
| `goal` | str | "Book a table for 4, Sat 25 Oct, 20:00, inside" |
| `user_facts` | dict[str, str] | Booking name, callback number, etc. |
| `allowed_decisions` | list[str] | What the agent may accept alone, e.g. "time within 30 min of 20:00" |
| `must_ask_user_for` | list[str] | Always ask, e.g. "any deposit or fee" |
| `max_call_seconds` | int | Hard stop, default 240 |

## CallEvent: call agent -> server

`POST /calls/{call_id}/events`

| Field | Type | Meaning |
| --- | --- | --- |
| `call_id` | str | Must match the path |
| `type` | `started`, `question`, `progress`, `result`, `failed` | |
| `text` | str | Human-readable line. For `question`, the question for the user |
| `options` | list[str] or null | `question` only: 2 to 4 poll options |
| `outcome` | `success`, `partial`, `failed` or null | `result` only |
| `details` | dict[str, str] or null | Agreed details, e.g. `{"time": "21:00"}` |
| `transcript` | list of `{"speaker": "agent" or "business", "text"}` or null | `result` only |

## Mid-call answer

`GET /calls/{call_id}/answer?wait=40` holds the request until the user votes, then returns `{"answer": "Yes"}`, or `{"answer": null}` after `wait` seconds (capped at 60).

- A new `question` event clears any earlier answer.
- Each answer is returned once.
