"""Server settings from the environment. Each adapter picks real or fake by its *_MODE variable."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Real env vars (e.g. from docker compose) win over .env.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


MESSENGER_MODE = env("MESSENGER_MODE", "fake")   # fake | linq
STT_MODE = env("STT_MODE", "fake")               # fake | scribe
CALLS_MODE = env("CALLS_MODE", "fake")           # fake | livekit
SMS_MODE = env("SMS_MODE", "fake")               # fake | twilio (dashboard login codes)
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
TWILIO_SMS_FROM = env("TWILIO_SMS_FROM") or env("TWILIO_NUMBER")  # must be SMS-capable
TWILIO_MESSAGING_SERVICE_SID = env("TWILIO_MESSAGING_SERVICE_SID")  # optional, wins over TWILIO_SMS_FROM
AUTH_SECRET = env("AUTH_SECRET")                 # signs dashboard sessions; empty = random per restart
# Host the dashboard PWA is served from. Adds "@host #code" to the login SMS so
# iOS and Android can autofill the code (origin-bound one-time codes, WebOTP). Empty: plain SMS.
APP_DOMAIN = env("APP_DOMAIN", "jamie-agentic-hack.app")
DATABASE_URL = env("DATABASE_URL")               # empty = no database (fakes only)
CALL_AGENT_NAME = env("CALL_AGENT_NAME", "jamie-call-agent")
LINQ_API_KEY = env("LINQ_API_KEY")
LINQ_WEBHOOK_SECRET = env("LINQ_WEBHOOK_SECRET")     # whsec_...; empty = signatures not checked
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
SERVER_PUBLIC_URL = env("SERVER_PUBLIC_URL").rstrip("/")
# Where the fake call posts its events. The real call agent uses SERVER_PUBLIC_URL.
LOCAL_SERVER_URL = env("LOCAL_SERVER_URL", "http://127.0.0.1:8000")


def allowed_call_numbers() -> set[str]:
    return {n.strip() for n in env("ALLOWED_CALL_NUMBERS").split(",") if n.strip()}
