"""SAFE_MODE: where a call really goes. Shared by server/ and call_agent/ so both agree.

Search and everything the user sees work the same in both modes; only the dialled number differs.
SAFE_MODE on (default): a number outside ALLOWED_CALL_NUMBERS is redirected to a stand-in from that
  list (SAFE_MODE_NUMBER, else the first allowed number), so a teammate plays the restaurant.
SAFE_MODE off: the restaurant's own number is dialled (any valid E.164), and the call agent always
  says it is a digital assistant (see call_agent/prompts.py).
"""
import os
import re

# E.164 with at least 8 digits: rules out short codes and emergency numbers like +49112.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class NotDialable(Exception):
    pass


def safe_mode() -> bool:
    """On unless SAFE_MODE is explicitly false/0/no/off."""
    return os.getenv("SAFE_MODE", "true").strip().lower() not in ("0", "false", "no", "off")


def allowed_numbers() -> set[str]:
    return {n.strip() for n in os.getenv("ALLOWED_CALL_NUMBERS", "").split(",") if n.strip()}


def standin_number() -> str | None:
    """The allowed number that plays every restaurant in safe mode."""
    allowed = allowed_numbers()
    chosen = os.getenv("SAFE_MODE_NUMBER", "").strip()
    if chosen in allowed:
        return chosen
    return sorted(allowed)[0] if allowed else None


def number_to_dial(number: str) -> str:
    """The number to actually dial for this business number. Raises NotDialable."""
    if safe_mode():
        if number in allowed_numbers():
            return number
        standin = standin_number()
        if standin is None:
            raise NotDialable("safe mode is on and ALLOWED_CALL_NUMBERS is empty")
        return standin
    if not _E164_RE.match(number or ""):
        raise NotDialable("not a valid international phone number")
    return number


def refusal_reason(number: str) -> str | None:
    """Last check right before dialling (call agent). The server already redirected in safe mode,
    so here a non-allowed number in safe mode means something went wrong."""
    if safe_mode():
        return None if number in allowed_numbers() else "number is not on the allowed list (safe mode is on)"
    return None if _E164_RE.match(number or "") else "not a valid international phone number"
