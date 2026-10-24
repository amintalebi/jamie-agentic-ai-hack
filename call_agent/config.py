"""Call-agent settings read from the environment (call after load_dotenv)."""
import os

DEFAULT_CALL_LLM_MODEL = "claude-haiku-4-5"  # faster TTFT than 5.5 in our tests (~0.6 s vs ~0.85 s)


def call_llm_model() -> str:
    return os.getenv("CALL_LLM_MODEL") or DEFAULT_CALL_LLM_MODEL
