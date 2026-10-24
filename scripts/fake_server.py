"""Dev stand-in for the server's call routes. Prints every CallEvent it receives and answers
GET /calls/{id}/answer from stdin (you type the user's poll answer) or from --auto.

    python scripts/fake_server.py [--port 8001] [--auto Yes] [--auto-delay 1]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "call_agent"))

from contracts_local import CallEvent  # noqa: E402  TODO(hack): switch to server.contracts
from events import mask_text  # noqa: E402

args: argparse.Namespace
app = FastAPI()
answers: dict[str, str] = {}
ready: dict[str, asyncio.Event] = {}


def _ready(call_id: str) -> asyncio.Event:
    return ready.setdefault(call_id, asyncio.Event())


async def _provide_answer(call_id: str, question: str, options: list[str]) -> None:
    if args.auto is not None:
        await asyncio.sleep(args.auto_delay)
        answer = args.auto
    else:
        answer = (await asyncio.to_thread(input, f"answer for {call_id} {options}> ")).strip()
    print(f"[ANSWER] {call_id}: {answer!r}", flush=True)
    answers[call_id] = answer
    _ready(call_id).set()


@app.post("/calls/{call_id}/events")
async def post_event(call_id: str, event: CallEvent) -> dict:
    body = json.dumps(event.model_dump(exclude_none=True), ensure_ascii=False, indent=2)
    print(f"\n[EVENT {event.type}] {call_id}\n{mask_text(body)}", flush=True)
    if event.type == "question":
        answers.pop(call_id, None)
        _ready(call_id).clear()
        asyncio.create_task(_provide_answer(call_id, event.text, event.options or []))
    return {"ok": True}


@app.get("/calls/{call_id}/answer")
async def get_answer(call_id: str, wait: int = 40) -> dict:
    try:
        await asyncio.wait_for(_ready(call_id).wait(), timeout=wait)
    except asyncio.TimeoutError:
        print(f"[ANSWER] {call_id}: timeout after {wait}s", flush=True)
        return {"answer": None}
    _ready(call_id).clear()
    return {"answer": answers.pop(call_id, None)}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--auto", help="answer every question with this text instead of reading stdin")
    p.add_argument("--auto-delay", type=float, default=1.0, help="seconds before the --auto answer")
    args = p.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
