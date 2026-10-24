"""Gateway smoke test (milestone 1): send a text and a poll to a phone over Linq.

    .venv/bin/python scripts/send_test.py --to +49151XXXXXXX
    .venv/bin/python scripts/send_test.py --to +49151XXXXXXX --text-only

Needs LINQ_API_KEY in .env. To see the vote arrive, run the server with MESSENGER_MODE=linq and a
webhook registered (scripts/linq_webhook.py create), then tap an option: the server logs
"linq webhook poll.vote.added" followed by the routed vote.
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.masking import mask_phone  # noqa: E402
from server import settings  # noqa: E402  (loads .env)
from server.gateway import LinqMessenger  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--to", required=True, help="recipient, E.164")
    p.add_argument("--text-only", action="store_true", help="skip the poll")
    args = p.parse_args()
    if not settings.LINQ_API_KEY:
        sys.exit("LINQ_API_KEY is not set in .env")

    linq = LinqMessenger()
    await linq.send_text(args.to, "Hi, this is Jamie. Gateway test.")
    print(f"text sent to {mask_phone(args.to)}")
    if not args.text_only:
        poll_id = await linq.send_poll(args.to, "Inside or outside?", ["Inside", "Outside"])
        print(f"poll sent, id {poll_id}" if poll_id else "poll failed, sent numbered options instead")


if __name__ == "__main__":
    asyncio.run(main())
