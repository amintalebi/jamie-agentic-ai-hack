"""SAFE_MODE: which numbers may be dialled. Shared by server/ and call_agent/ so both agree.

SAFE_MODE on (default): only numbers in ALLOWED_CALL_NUMBERS (our teammates playing the restaurant).
SAFE_MODE off: any valid E.164 number, so Eve can call real restaurants found by search. The call
agent then always says it is a digital assistant (see call_agent/prompts.py).
"""
import os
import re

# E.164 with at least 8 digits: rules out short codes and emergency numbers like +49112.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def safe_mode() -> bool:
    """On unless SAFE_MODE is explicitly false/0/no/off. Read at call time, so a restart is not needed in tests."""
    return os.getenv("SAFE_MODE", "true").strip().lower() not in ("0", "false", "no", "off")


def allowed_numbers() -> set[str]:
    return {n.strip() for n in os.getenv("ALLOWED_CALL_NUMBERS", "").split(",") if n.strip()}


def refusal_reason(number: str) -> str | None:
    """None if this number may be dialled now, otherwise a short reason."""
    if safe_mode():
        return None if number in allowed_numbers() else "number is not on the allowed list (safe mode is on)"
    return None if _E164_RE.match(number or "") else "not a valid international phone number"
