"""Monitoring dashboard API (owner: D).

Screens are sent as OpenUI Lang (https://openui.com), built here in code, no LLM involved.
The PWA in web/ registers the matching components and renders them with <Renderer>.
Component signatures here must match web/src/library.tsx: arguments are positional,
in the order of each component's Zod schema keys.

    python -m server.dashboard web/src/__fixtures__   # dump sample screens for the web tests
"""
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from common.masking import mask_phone
from server import history, settings

log = logging.getLogger("server.dashboard")
router = APIRouter(prefix="/api/dashboard")

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
LIVE_STATUSES = {"dialing", "active", "waiting_user"}


# --- auth --------------------------------------------------------------------

def require_token(authorization: str = Header(default="")) -> None:
    token = settings.DASHBOARD_TOKEN
    if not token:
        if settings.MESSENGER_MODE == "fake" and settings.CALLS_MODE == "fake":
            return  # local dev on fakes: open
        raise HTTPException(503, "set DASHBOARD_TOKEN to use the dashboard")
    given = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "bad dashboard token")


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


def _duration_s(call: dict) -> int:
    end = call.get("ended_at") or datetime.now(timezone.utc)
    return max(0, int((end - call["created_at"]).total_seconds()))


def _subtitle(call: dict) -> str:
    brief = call["brief"]
    return f"for {brief.get('caller_name', '?')} · {mask_phone(brief.get('to_number', ''))}"


def calls_screen(calls: list[dict]) -> tuple[str, bool]:
    p = Program()
    today = datetime.now(timezone.utc).date()
    live = [c for c in calls if c["status"] in LIVE_STATUSES]
    stats = p.add("StatRow", [
        p.add("Stat", "Live now", str(len(live)), "live" if live else "neutral"),
        p.add("Stat", "Today", str(sum(c["created_at"].date() == today for c in calls)), "neutral"),
        p.add("Stat", "Succeeded", str(sum(c.get("outcome") == "success" for c in calls)), "good"),
        p.add("Stat", "Failed", str(sum(c["status"] == "failed" or c.get("outcome") == "failed" for c in calls)), "bad"),
    ])
    if calls:
        body = p.add("CallList", [
            p.add("CallRow", c["call_id"], c["brief"].get("goal", ""), _subtitle(c), c["status"],
                  c.get("outcome"), _iso(c["created_at"]), _duration_s(c))
            for c in calls
        ])
    else:
        body = p.add("Empty", "No calls yet. Ask Jamie to call somewhere and it shows up here live.")
    return p.render("Page", "Calls", "Live call monitor", [stats, body]), bool(live)


_EVENT_LABELS = {"placed": "Placed", "started": "Connected", "question": "Asked you", "answer": "You answered",
                 "progress": "Progress", "poll_failed": "Poll failed", "result": "Result", "failed": "Failed"}


def call_screen(call: dict) -> tuple[str, bool]:
    p = Program()
    brief = call["brief"]
    live = call["status"] in LIVE_STATUSES
    children = [
        p.add("BackLink", "All calls", "#/"),
        p.add("CallHeader", brief.get("goal", ""), _subtitle(call), call["status"], call.get("outcome"),
              brief.get("language", ""), _iso(call["created_at"]), _duration_s(call),
              int(brief.get("max_call_seconds", 240))),
    ]
    events = call.get("events", [])
    if call["status"] == "waiting_user":
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
    if call.get("transcript"):
        children.append(p.add("Section", "Transcript", [
            p.add("TranscriptLine", "agent" if t.get("speaker") == "agent" else "business", str(t.get("text", "")))
            for t in call["transcript"]]))
    children.append(p.add("Section", "Brief", [
        p.add("KeyValue", "Allowed alone", "; ".join(brief.get("allowed_decisions", [])) or "nothing"),
        p.add("KeyValue", "Must ask you", "; ".join(brief.get("must_ask_user_for", [])) or "nothing"),
    ]))
    return p.render("Page", "Call", call["call_id"], children), live


# --- routes --------------------------------------------------------------------

@router.get("/calls", dependencies=[Depends(require_token)])
async def calls_view(limit: int = 50) -> dict:
    ui, live = calls_screen(await history.list_calls(min(max(limit, 1), 200)))
    return {"ui": ui, "live": live}


@router.get("/calls/{call_id}", dependencies=[Depends(require_token)])
async def call_view(call_id: str) -> dict:
    call = await history.get_call(call_id)
    if call is None:
        raise HTTPException(404, "unknown call")
    ui, live = call_screen(call)
    return {"ui": ui, "live": live}


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
    t0 = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
    brief = {"call_id": "abc123", "user_id": "+4917600005678", "to_number": "+4915100001234", "language": "de",
             "caller_name": "Sina", "goal": "Book a table for 4, tomorrow, 20:00, inside",
             "user_facts": {}, "allowed_decisions": ["time within 30 min of 20:00"],
             "must_ask_user_for": ["any deposit or fee"], "max_call_seconds": 240}
    ev = lambda type, text, s, options=None: {"type": type, "text": text, "options": options,  # noqa: E731
                                              "created_at": t0.replace(second=s)}
    waiting = {"call_id": "abc123", "brief": brief, "status": "waiting_user", "outcome": None, "summary": None,
               "details": None, "transcript": None, "created_at": t0, "ended_at": None,
               "events": [ev("placed", "Call placed", 0), ev("started", "Ringing the restaurant", 1),
                          ev("question", 'They offer 21:00 instead of 20:00. "OK"?', 3, ["Yes", "No"])]}
    done = {**waiting, "call_id": "def456", "status": "ended", "outcome": "success",
            "summary": "Table for 4 booked at 21:00", "details": {"time": "21:00", "persons": "4"},
            "transcript": [{"speaker": "business", "text": "Ristorante, guten Tag?"},
                           {"speaker": "agent", "text": "Hallo, ich rufe für Sina an."}],
            "ended_at": t0.replace(minute=2),
            "events": waiting["events"] + [ev("answer", "Yes", 9), ev("result", "Table for 4 booked at 21:00", 20)]}
    return {
        "calls.oui": calls_screen([waiting, done])[0],
        "calls_empty.oui": calls_screen([])[0],
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
