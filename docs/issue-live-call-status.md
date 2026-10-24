# Issue: the user hears nothing while a call is running

**Owner of the fix:** D (`server/calls.py`)
**Raised by:** C (orchestrator and memory)
**Severity:** cosmetic, the booking itself works. Polish item, not a blocker.

## What the user sees today

```
Jamie   Calling Trattoria da Demo now to book a table for 4, Sun 25 Oct, 20:00, inside.
                                  ... 30 to 60 seconds of nothing ...
Jamie   On the call: They offer 21:00 instead of 20:00. OK?
```

Between placing the call and the first mid-call question there is no signal at all. On a real
call that gap is long enough that the demo looks frozen.

## Why

`calls.after_event` only reacts to two of the four event types the call agent can send:

| event | `apply_event` | `after_event` | user sees |
| --- | --- | --- | --- |
| `started` | status -> active | nothing | nothing |
| `progress` | nothing | nothing | nothing |
| `question` | stores the question | sends a poll | the poll |
| `result` / `failed` | status -> ended | `orchestrator.handle_call_result` | the summary |

`progress` is in the `CallEvent` contract and the call agent may emit it, but it is dropped
silently. `started` only updates internal state.

## Why C cannot fix this from the orchestrator

The orchestrator only runs when an inbound user message arrives. During a call it is not
running at all: `started` and `progress` go to `calls.py`, and even the mid-call poll vote
goes `gateway.route_inbound -> calls.match_vote -> calls.resolve_answer` without passing
through the orchestrator. The next time the orchestrator hears anything is
`handle_call_result`, after the call has already ended.

So the live middle of the status bubble can only be driven from `after_event`.

## What C has already done

`server/orchestrator.py` has a best-effort `_set_status` helper and uses it at both ends:

- opens the bubble with `new=True` right after `calls.place_call` succeeds ("Calling X now...")
- writes the closing line in `handle_call_result` ("Booked: 21:00" / "Could not book it")

`_set_status` is deliberately defensive: `gateway.set_status` only exists once the iMessage
extras from `main` are merged, so before that it is a no-op, and any failure is logged and
swallowed. Nothing about the call depends on it.

## Suggested fix in `calls.py`

Add the two missing branches to `after_event`, in the same best-effort style as the existing
poll handling:

```python
    if event.type == "started":
        await _status(user_id, "On the phone with them")
    elif event.type == "progress":
        await _status(user_id, event.text)
    elif event.type == "question":
        await _status(user_id, f"On hold: {event.text}")
        ...existing poll code...
```

with a local helper that never raises:

```python
async def _status(user: str, text: str) -> None:
    try:
        await gateway.set_status(user, text)
    except Exception as e:
        log.warning("status update failed: %s", e)
```

## Two constraints to respect

1. **Edit budget.** Linq allows 5 edits per message within 15 minutes
   (`LinqMessenger.STATUS_MAX_EDITS = 5`, `STATUS_MAX_AGE_S = 14 * 60`). Past either limit
   `set_status` silently starts a *new* bubble instead of failing. The orchestrator already
   spends 2 of the 5 (open and close), so `calls.py` has about 3 left. If the call agent sends
   many `progress` events, throttle them or only forward the first one.
2. **Never block the call.** A status edit is an HTTP call to Linq. It must be wrapped in
   try/except exactly like the existing poll fallback, because the business is on the line.

## Prerequisite

`gateway.set_status` arrives with the "iMessage extras for the orchestrator" commit on `main`
(`d588e10`). On a branch without that merge, this whole feature is inert by design.
