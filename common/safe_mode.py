"""SAFE_MODE: where a call really goes. The server decides (plan_dial) and sends the decision to the
call agent with the job (DialPlan in the dispatch metadata), so the two can never disagree.

Search and everything the user sees work the same in both modes; only the dialled number differs.
The brief, call history and memory always keep the business's own number.
SAFE_MODE on (default): every call rings SAFE_MODE_NUMBER (a teammate playing the business).
  Unset SAFE_MODE_NUMBER = every call is refused.
SAFE_MODE off: the business's own number is dialled (valid E.164, no premium-rate or service
  numbers), and the call agent says it is a digital assistant.
"""
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger("common.safe_mode")

# E.164 with at least 8 digits: rules out short codes and emergency numbers like +49112.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

# Premium-rate, paid service and directory prefixes we never dial.
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


def safe_mode_number() -> str:
    """SAFE_MODE_NUMBER normalised, or "" when unset."""
    return normalize(os.getenv("SAFE_MODE_NUMBER", ""))


def number_problem(number: str) -> str | None:
    """None if this number may be dialled at all, otherwise why not."""
    if not _E164_RE.match(number):
        return "not a valid international phone number"
    if number.startswith(_BLOCKED_PREFIXES):
        return "premium rate or service number"
    return None


def plan_dial(business_number: str) -> DialPlan:
    """Server side: decide what to dial for this business number. Raises NotDialable."""
    if safe_mode():
        standin = safe_mode_number()
        if not standin:
            raise NotDialable("safe mode is on but SAFE_MODE_NUMBER is not set")
        problem = number_problem(standin)
        if problem:
            log.error("SAFE_MODE_NUMBER is invalid (%s), refusing calls until fixed", problem)
            raise NotDialable(f"SAFE_MODE_NUMBER is invalid: {problem}")
        return DialPlan(standin, real_business=False)
    number = normalize(business_number)
    problem = number_problem(number)
    if problem:
        raise NotDialable(problem)
    return DialPlan(number, real_business=True)


def check_plan(plan: DialPlan) -> str | None:
    """Call agent side, right before dialling: None if OK, otherwise why not.
    Follows the server's decision; does not read SAFE_MODE itself."""
    return number_problem(plan.dial_number)
