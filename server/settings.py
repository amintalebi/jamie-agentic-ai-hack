"""Server settings from the environment. Each adapter picks real or fake by its *_MODE variable."""
import os


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


MESSENGER_MODE = env("MESSENGER_MODE", "fake")   # fake | linq
STT_MODE = env("STT_MODE", "fake")               # fake | scribe
CALLS_MODE = env("CALLS_MODE", "fake")           # fake | livekit
DATABASE_URL = env("DATABASE_URL")               # empty = no database (fakes only)
CALL_AGENT_NAME = env("CALL_AGENT_NAME", "jamie-call-agent")
# Where the fake call posts its events. The real call agent uses SERVER_PUBLIC_URL.
LOCAL_SERVER_URL = env("LOCAL_SERVER_URL", "http://127.0.0.1:8000")


def allowed_call_numbers() -> set[str]:
    return {n.strip() for n in env("ALLOWED_CALL_NUMBERS").split(",") if n.strip()}
