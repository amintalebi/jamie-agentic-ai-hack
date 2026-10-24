"""Monitoring dashboard API (owner: D).

Screens are sent as OpenUI Lang (https://openui.com), built here in code, no LLM involved.
The PWA in web/ registers the matching components and renders them with <Renderer>.
Component signatures here must match web/src/library.tsx: arguments are positional,
in the order of each component's Zod schema keys.

    python -m server.dashboard web/src/__fixtures__   # dump sample screens for the web tests
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from common.masking import mask_phone
from server import auth, calls, history, live

log = logging.getLogger("server.dashboard")
router = APIRouter(prefix="/api/dashboard")

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


# --- OpenUI Lang builder -------------------------------------------------------

class Ref(str):
    """Name of a statement in the program."""


class Program:
    """Collects `name = Component(args...)` statements; render() puts `root` first."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, component: str, *args) -> Ref:
        ref = Ref(f"n{len(self.lines)}")
        self.lines.append(f"{ref} = {component}({', '.join(lit(a) for a in args)})")
        return ref

    def render(self, root_component: str, *args) -> str:
        return "\n".join([f"root = {root_component}({', '.join(lit(a) for a in args)})", *self.lines])


def lit(value) -> str:
    if isinstance(value, Ref):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(lit(v) for v in value) + "]"
    raise TypeError(f"cannot serialise {type(value).__name__} to OpenUI Lang")


# --- screens -------------------------------------------------------------------

def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _status(call: dict) -> str:
    """Stored status, or "lost" for a call still marked live long past its time limit."""
    return "lost" if history.is_stale(call) else call["status"]


def _is_live(call: dict) -> bool:
    return _status(call) in history.LIVE_STATUSES


def _duration_s(call: dict) -> int:
    if call.get("ended_at"):
        end = call["ended_at"]
    elif _status(call) == "lost":
        end = call.get("updated_at") or call["created_at"]  # last sign of life
    else:
        end = datetime.now(timezone.utc)
    return max(0, int((end - call["created_at"]).total_seconds()))


def _subtitle(call: dict) -> str:
    brief = call["brief"]
    return f"for {brief.get('caller_name', '?')} · {mask_phone(brief.get('to_number', ''))}"


def calls_screen(calls: list[dict], stats: dict[str, int]) -> tuple[str, bool]:
    """calls: the newest calls; stats: counts over all calls (history.stats)."""
    p = Program()
    stat_row = p.add("StatRow", [
        p.add("Stat", "Live now", str(stats["live"]), "live" if stats["live"] else "neutral"),
        p.add("Stat", "Last 24 h", str(stats["recent"]), "neutral"),
        p.add("Stat", "Succeeded", str(stats["succeeded"]), "good"),
        p.add("Stat", "Failed", str(stats["failed"]), "bad"),
    ])
    if calls:
        body = p.add("CallList", [
            p.add("CallRow", c["call_id"], c["brief"].get("goal", ""), _subtitle(c), _status(c),
                  c.get("outcome"), _iso(c["created_at"]), _duration_s(c))
            for c in calls
        ])
    else:
        body = p.add("Empty", "No calls yet. Ask Eve to call somewhere and it shows up here live.")
    return p.render("Page", "Calls", "Live call monitor", [stat_row, body]), any(_is_live(c) for c in calls)


_EVENT_LABELS = {"placed": "Placed", "started": "Connected", "question": "Asked you", "answer": "You answered",
                 "progress": "Progress", "poll_failed": "Poll failed", "result": "Result", "failed": "Failed",
                 "lost": "Lost contact"}


def call_screen(call: dict) -> tuple[str, bool]:
    p = Program()
    brief = call["brief"]
    status = _status(call)
    live = status in history.LIVE_STATUSES
    children = [
        p.add("BackLink", "All calls", "#/"),
        p.add("CallHeader", brief.get("goal", ""), _subtitle(call), status, call.get("outcome"),
              brief.get("language", ""), _iso(call["created_at"]), _duration_s(call),
              int(brief.get("max_call_seconds", 240))),
    ]
    if live:
        # Third child on purpose: the Page keys children by position, so the live transcript keeps
        # its place (and its open stream) while questions and timeline items come and go.
        children.append(p.add("LiveTranscript", call["call_id"]))
    events = call.get("events", [])
    if status == "waiting_user":
        q = next((e for e in reversed(events) if e["type"] == "question"), None)
        if q:
            children.append(p.add("PendingQuestion", q["text"], q.get("options") or []))
    if call.get("summary"):
        children.append(p.add("Note", call["summary"]))
    if call.get("details"):
        children.append(p.add("Section", "Agreed details", [
            p.add("KeyValue", str(k), str(v)) for k, v in call["details"].items()]))
    children.append(p.add("Section", "Timeline", [
        p.add("TimelineItem", e["type"], _EVENT_LABELS.get(e["type"], e["type"]), e["text"], _iso(e["created_at"]))
        for e in events]))
    if call.get("transcript") and not live:
        children.append(p.add("Section", "Transcript", [
            p.add("TranscriptLine", "agent" if t.get("speaker") == "agent" else "business", str(t.get("text", "")))
            for t in call["transcript"]]))
    children.append(p.add("Section", "Brief", [
        p.add("KeyValue", "Allowed alone", "; ".join(brief.get("allowed_decisions", [])) or "nothing"),
        p.add("KeyValue", "Must ask you", "; ".join(brief.get("must_ask_user_for", [])) or "nothing"),
    ]))
    # Empty title: the CallHeader card already names this screen.
    return p.render("Page", "", None, children), live


# --- routes --------------------------------------------------------------------

@router.get("/calls")
async def calls_view(limit: int = 50, user: str = Depends(auth.current_user)) -> dict:
    calls = await history.list_calls(user, min(max(limit, 1), 200))
    ui, live = calls_screen(calls, await history.stats(user, history.recent_cutoff()))
    return {"ui": ui, "live": live}


@router.get("/calls/{call_id}")
async def call_view(call_id: str, user: str = Depends(auth.current_user)) -> dict:
    call = await history.get_call(call_id, user)
    if call is None:
        raise HTTPException(404, "unknown call")
    ui, live = call_screen(call)
    return {"ui": ui, "live": live}


@router.get("/calls/{call_id}/live")
async def call_live(call_id: str, user: str = Depends(auth.current_user)) -> StreamingResponse:
    """Server-Sent Events: the call's transcript as it happens (see server/live.py)."""
    call = await history.get_call(call_id, user)
    if call is None:
        raise HTTPException(404, "unknown call")
    return StreamingResponse(
        live.stream(call_id, is_live=lambda: calls.is_live(call_id)),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx in front of us must pass events through, not buffer them.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def mount_app(app: FastAPI) -> None:
    """Serve the built PWA (web/dist) at /app/."""
    if not (WEB_DIST / "index.html").exists():
        log.info("web/dist not built, dashboard app not served (API still at /api/dashboard)")
        return

    @app.get("/app", include_in_schema=False)
    async def app_redirect():
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=WEB_DIST, html=True), name="app")


# --- sample screens for the web tests -------------------------------------------

def _samples() -> dict[str, str]:
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=3)  # recent, so not "lost"
    brief = {"call_id": "abc123", "user_id": "+4917600005678", "to_number": "+4915100001234", "language": "de",
             "caller_name": "Sina", "goal": "Book a table for 4, tomorrow, 20:00, inside",
             "user_facts": {}, "allowed_decisions": ["time within 30 min of 20:00"],
             "must_ask_user_for": ["any deposit or fee"], "max_call_seconds": 240}
    ev = lambda type, text, s, options=None: {"type": type, "text": text, "options": options,  # noqa: E731
                                              "created_at": t0 + timedelta(seconds=s)}
    waiting = {"call_id": "abc123", "brief": brief, "status": "waiting_user", "outcome": None, "summary": None,
               "details": None, "transcript": None, "created_at": t0, "ended_at": None,
               "events": [ev("placed", "Call placed", 0), ev("started", "Ringing the restaurant", 1),
                          ev("question", 'They offer 21:00 instead of 20:00. "OK"?', 3, ["Yes", "No"])]}
    done = {**waiting, "call_id": "def456", "status": "ended", "outcome": "success",
            "summary": "Table for 4 booked at 21:00", "details": {"time": "21:00", "persons": "4"},
            "transcript": [{"speaker": "business", "text": "Ristorante, guten Tag?"},
                           {"speaker": "agent", "text": "Hallo, ich rufe für Sina an."}],
            "ended_at": t0 + timedelta(minutes=2),
            "events": waiting["events"] + [ev("answer", "Yes", 9), ev("result", "Table for 4 booked at 21:00", 20)]}
    lost = {**waiting, "call_id": "ghi789", "status": "active", "created_at": t0 - timedelta(hours=1),
            "updated_at": t0 - timedelta(minutes=58)}
    stats = {"live": 1, "recent": 3, "succeeded": 1, "failed": 1}
    return {
        "calls.oui": calls_screen([waiting, done, lost], stats)[0],
        "calls_empty.oui": calls_screen([], {"live": 0, "recent": 0, "succeeded": 0, "failed": 0})[0],
        "call_lost.oui": call_screen({**lost, "events": waiting["events"][:2]})[0],
        "call_waiting.oui": call_screen(waiting)[0],
        "call_done.oui": call_screen(done)[0],
    }


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "web/src/__fixtures__")
    out.mkdir(parents=True, exist_ok=True)
    for name, text in _samples().items():
        (out / name).write_text(text + "\n", encoding="utf-8")
        print("wrote", out / name)
