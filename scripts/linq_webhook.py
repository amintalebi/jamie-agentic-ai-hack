"""Register the server's webhook with Linq, list registered webhooks, or delete one.

    .venv/bin/python scripts/linq_webhook.py list
    .venv/bin/python scripts/linq_webhook.py create              # uses SERVER_PUBLIC_URL from .env
    .venv/bin/python scripts/linq_webhook.py create --url https://<tunnel>.trycloudflare.com
    .venv/bin/python scripts/linq_webhook.py delete <subscription_id>

create points Linq at <url>/webhooks/linq?version=2026-02-03 (the payload version the gateway
parses) and writes the signing secret into .env as LINQ_WEBHOOK_SECRET; Linq shows it only once.
Restart the server afterwards so it picks the secret up.
"""
import argparse
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
load_dotenv(ENV)
sys.path.insert(0, str(ROOT))

from server import settings  # noqa: E402
from server.gateway import LINQ_BASE_URL  # noqa: E402

EVENTS = ["message.received", "poll.vote.added", "reaction.added"]
VERSION = "2026-02-03"


def client() -> httpx.Client:
    if not settings.LINQ_API_KEY:
        sys.exit("LINQ_API_KEY is not set in .env")
    return httpx.Client(base_url=LINQ_BASE_URL, timeout=15,
                        headers={"Authorization": f"Bearer {settings.LINQ_API_KEY}"})


def save_secret(secret: str) -> None:
    text = ENV.read_text()
    line = f"LINQ_WEBHOOK_SECRET={secret}"
    if re.search(r"^LINQ_WEBHOOK_SECRET=", text, re.M):
        text = re.sub(r"^LINQ_WEBHOOK_SECRET=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV.write_text(text)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create")
    c.add_argument("--url", default=settings.SERVER_PUBLIC_URL, help="public HTTPS base URL of the server")
    d = sub.add_parser("delete")
    d.add_argument("id")
    args = p.parse_args()

    with client() as http:
        if args.cmd == "list":
            r = http.get("/webhook-subscriptions")
            r.raise_for_status()
            for s in r.json().get("subscriptions", []):
                print(s["id"], "active" if s.get("is_active") else "inactive", s["target_url"], s.get("subscribed_events"))
        elif args.cmd == "create":
            if not args.url.startswith("https://"):
                sys.exit("need an https:// URL: pass --url or set SERVER_PUBLIC_URL")
            target = f"{args.url.rstrip('/')}/webhooks/linq?version={VERSION}"
            r = http.post("/webhook-subscriptions", json={"target_url": target, "subscribed_events": EVENTS})
            if r.is_error:
                sys.exit(f"create failed: {r.status_code} {r.text[:300]}")
            sub_ = r.json()
            save_secret(sub_["signing_secret"])
            print(f"created {sub_['id']} -> {target}\nsigning secret saved to .env as LINQ_WEBHOOK_SECRET")
        else:
            r = http.delete(f"/webhook-subscriptions/{args.id}")
            print("deleted" if r.is_success else f"delete failed: {r.status_code} {r.text[:300]}")


if __name__ == "__main__":
    main()
