"""Live test of the orchestrator's iMessage tools (server/gateway_tools.py) against a real phone.

    .venv/bin/python scripts/imessage_extras_test.py --to +49151XXXXXXX            # all of them
    .venv/bin/python scripts/imessage_extras_test.py --to +49151XXXXXXX --only status react

Runs each tool through gateway_tools.run, the same entry point the orchestrator uses:
  status    a status bubble that edits itself through a fake call (4 updates, 3 s apart)
  confetti  "Booked" text with the confetti effect
  calendar  an .ics invite for tomorrow 21:00 (tap it on the phone: "Add to Calendar")
  react     a tapback on your latest message (send the number a text first)
Needs LINQ_API_KEY in .env.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["MESSENGER_MODE"] = "linq"  # before importing server.settings
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import gateway_tools, settings  # noqa: E402

STEPS = ["status", "confetti", "calendar", "react"]


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--to", required=True, help="recipient, E.164")
    p.add_argument("--only", nargs="+", choices=STEPS, default=STEPS)
    args = p.parse_args()
    if not settings.LINQ_API_KEY:
        sys.exit("LINQ_API_KEY is not set in .env")

    async def tool(name: str, **kw) -> None:
        print(f"{name} {kw}: {await gateway_tools.run(args.to, name, kw)}")

    if "status" in args.only:
        await tool("update_status", text="📞 Calling Trattoria Da Enzo...", new=True)
        for line in ["🗣 On the phone with them", "⏸ On hold: they offer 21:00 instead of 20:00",
                     "✅ Booked: table for 4, tomorrow 21:00"]:
            await asyncio.sleep(3)
            await tool("update_status", text=line)
    if "confetti" in args.only:
        await tool("send_message", text="You're all set: table for 4 at Da Enzo, tomorrow at 21:00.", effect="confetti")
    if "calendar" in args.only:
        start = (datetime.now() + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
        await tool("send_calendar_invite", title="Dinner at Trattoria Da Enzo (4 people)",
                   start=start.isoformat(timespec="minutes"), location="Trattoria Da Enzo, Berlin",
                   notes="Booked by Jamie for Sina, table inside")
    if "react" in args.only:
        await tool("react", reaction="love")


if __name__ == "__main__":
    asyncio.run(main())
