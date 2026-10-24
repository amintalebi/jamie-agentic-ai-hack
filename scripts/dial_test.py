"""Telephony smoke test (milestone 1): dispatch the call agent into a new room so it dials a phone.

The worker must be running in another terminal:
    .venv/bin/python call_agent/agent.py dev

Then:
    .venv/bin/python scripts/dial_test.py --to +49151XXXXXXX                 # hello-world call, German
    .venv/bin/python scripts/dial_test.py --to +49151XXXXXXX --lang en
    .venv/bin/python scripts/dial_test.py --to +49151XXXXXXX --brief fixtures/brief_italian.json

Refuses any number not in ALLOWED_CALL_NUMBERS. Prints the dispatch, then watches the room's
participants (and the SIP call status) until the call ends or --watch seconds pass.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "call_agent"))
load_dotenv(ROOT / ".env")

from livekit import api  # noqa: E402

from contracts_local import CallBrief  # noqa: E402  TODO(hack): switch to server.contracts
from events import mask_phone  # noqa: E402

AGENT_NAME = "jamie-call-agent"


def allowed_numbers() -> set[str]:
    raw = os.getenv("ALLOWED_CALL_NUMBERS", "")
    return {n.strip() for n in raw.split(",") if n.strip()}


def hello_brief(to_number: str, lang: str, call_id: str) -> CallBrief:
    """Minimal brief for the 14:30 gate: the agent greets, asks one question, says goodbye."""
    return CallBrief(
        call_id=call_id,
        user_id="dialtest",
        to_number=to_number,
        language=lang,
        caller_name="Sina",
        goal="Telephony test call. Greet the person, ask how their day is going, thank them and end the call.",
        user_facts={"name": "Sina"},
        allowed_decisions=["anything, this is only a test"],
        must_ask_user_for=[],
        max_call_seconds=90,
    )


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--to", required=True, help="E.164 number to dial, must be in ALLOWED_CALL_NUMBERS")
    p.add_argument("--lang", choices=["de", "en"], default="de")
    p.add_argument("--brief", help="path to a CallBrief JSON fixture; to_number and call_id are overridden")
    p.add_argument("--watch", type=int, default=150, help="seconds to watch the room before exiting")
    args = p.parse_args()

    if args.to not in allowed_numbers():
        print(f"refused: {mask_phone(args.to)} is not in ALLOWED_CALL_NUMBERS", file=sys.stderr)
        return 2
    for var in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_SIP_OUTBOUND_TRUNK_ID"):
        if not os.getenv(var):
            print(f"missing {var} in .env", file=sys.stderr)
            return 2

    call_id = f"dialtest-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    if args.brief:
        brief = CallBrief.model_validate_json(Path(args.brief).read_text())
        brief = brief.model_copy(update={"to_number": args.to, "call_id": call_id, "language": args.lang})
        metadata = brief.model_dump()
    else:
        brief = hello_brief(args.to, args.lang, call_id)
        metadata = {**brief.model_dump(), "mode": "hello"}  # agent.py swaps in a tiny hello prompt

    room = f"call-{call_id}"
    async with api.LiveKitAPI() as lk:
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room, metadata=json.dumps(metadata))
        )
        print(f"dispatched {dispatch.id} agent={AGENT_NAME} room={room} to={mask_phone(args.to)} lang={args.lang}")
        print("watching the room; the worker terminal shows the conversation and latency lines")

        deadline = time.time() + args.watch
        seen_sip = False
        while time.time() < deadline:
            await asyncio.sleep(2)
            try:
                res = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
            except api.TwirpError as e:
                print(f"room closed ({e.message}); call over")
                return 0
            parts = []
            for part in res.participants:
                status = part.attributes.get("sip.callStatus", "")
                if status:
                    seen_sip = True
                parts.append(f"{part.identity}{'=' + status if status else ''}")
            print(f"{int(deadline - time.time()):4d}s  participants: {', '.join(parts) or '(none yet)'}")
            if seen_sip and not any("sip.callStatus" in part.attributes for part in res.participants):
                print("SIP participant left; call over")
                return 0
        print("watch window over; the call may still be running")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
