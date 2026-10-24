"""SAFE_MODE: where a call really goes. The server decides (plan_dial) and sends the decision to the
call agent with the job (DialPlan in the dispatch metadata), so the two can never disagree.

Search and everything the user sees work the same in both modes; only the dialled number differs.
The brief, call history and memory always keep the business's own number.
SAFE_MODE on (default): a number outside ALLOWED_CALL_NUMBERS is dialled as a stand-in from that list
  (SAFE_MODE_NUMBER, else the first allowed number), so a teammate plays the restaurant.
SAFE_MODE off: the restaurant's own number is dialled (valid E.164, no premium-rate or service
  numbers), and the call agent says it is a digital assistant.
"""
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger("common.safe_mode")

# E.164 with at least 8 digits: rules out short codes and emergency numbers like +49112.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

# Premium-rate, paid service and directory prefixes we never dial for real.
# TODO(hack): only DE/AT/CH/UK/FR/US/NL covered; extend before calling other countries.
_BLOCKED_PREFIXES = (
    "+49900", "+49137", "+49138", "+49118", "+49180", "+49190", "+49199", "+49700",  # DE
    "+43900", "+43901", "+43930", "+43931", "+43939", "+43118",                       # AT
    "+41900", "+41901", "+41906", "+4118",                                            # CH
    "+449", "+4487", "+44118", "+4470",                                               # UK
    "+3389", "+33118",                                                                # FR
    "+1900", "+1976",                                                                 # US/CA
    "+31900", "+31906", "+31909", "+3118",                                            # NL
)


class NotDialable(Exception):
    pass


@dataclass(frozen=True)
class DialPlan:
    dial_number: str      # what the SIP trunk actually dials
    real_business: bool   # True: a real third party answers (AI disclosure on)


def normalize(number: str) -> str:
    """'+49 30 123-4567' -> '+49301234567'; '0049...' -> '+49...'. Other input is returned stripped."""
    n = re.sub(r"[\s\-./()]", "", number or "")
    if n.startswith("00"):
        n = "+" + n[2:]
    return n


def safe_mode() -> bool:
    """On unless SAFE_MODE is explicitly false/0/no/off. Read by the server only."""
    return os.getenv("SAFE_MODE", "true").strip().lower() not in ("0", "false", "no", "off")


def allowed_numbers() -> set[str]:
    return {normalize(n) for n in os.getenv("ALLOWED_CALL_NUMBERS", "").split(",") if n.strip()}


def standin_number() -> str:
    """The allowed number that plays every restaurant in safe mode. Raises NotDialable."""
    allowed = allowed_numbers()
    if not allowed:
        raise NotDialable("safe mode is on and ALLOWED_CALL_NUMBERS is empty")
    raw = os.getenv("SAFE_MODE_NUMBER", "").strip()
    if not raw:
        return sorted(allowed)[0]
    chosen = normalize(raw)
    if chosen not in allowed:
        # Refuse rather than ring some other allowed phone (maybe the user's own).
        log.error("SAFE_MODE_NUMBER is not in ALLOWED_CALL_NUMBERS, refusing calls until fixed")
        raise NotDialable("SAFE_MODE_NUMBER is not in ALLOWED_CALL_NUMBERS")
    return chosen


def real_number_problem(number: str) -> str | None:
    """None if this real number may be dialled, otherwise why not."""
    if not _E164_RE.match(number):
        return "not a valid international phone number"
    if number.startswith(_BLOCKED_PREFIXES):
        return "premium rate or service number"
    return None


def plan_dial(business_number: str) -> DialPlan:
    """Server side: decide what to dial for this (normalised) business number. Raises NotDialable."""
    number = normalize(business_number)
    allowed = allowed_numbers()
    if number in allowed:
        return DialPlan(number, real_business=False)
    if safe_mode():
        return DialPlan(standin_number(), real_business=False)
    problem = real_number_problem(number)
    if problem:
        raise NotDialable(problem)
    return DialPlan(number, real_business=True)


def check_plan(plan: DialPlan) -> str | None:
    """Call agent side, right before dialling: None if OK, otherwise why not.
    Follows the server's decision; does not read SAFE_MODE itself."""
    if plan.real_business:
        return real_number_problem(plan.dial_number)
    if plan.dial_number in allowed_numbers():
        return None
    return "number is not in the call agent's ALLOWED_CALL_NUMBERS (does it match the server's?)"
