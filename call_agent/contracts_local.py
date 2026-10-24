# TODO(hack): replace with server.contracts once D lands server/contracts.py.
# Copied verbatim from CLAUDE.md "Contracts"; do not change without telling the team.
from typing import Literal

from pydantic import BaseModel


class CallBrief(BaseModel):
    call_id: str
    user_id: str
    to_number: str                 # E.164
    language: Literal["de", "en"]
    caller_name: str               # who we call on behalf of, e.g. "Sina"
    goal: str                      # "Book a table for 4, Sat 25 Oct, 20:00, inside"
    user_facts: dict[str, str]     # name, callback number, booking name, etc.
    allowed_decisions: list[str]   # things the agent may accept without asking, e.g. "time within 30 min of 20:00"
    must_ask_user_for: list[str]   # always ask, e.g. "any deposit or fee", "different date"
    max_call_seconds: int = 240


class CallEvent(BaseModel):
    call_id: str
    type: Literal["started", "question", "progress", "result", "failed"]
    text: str                      # human-readable line
    options: list[str] | None = None        # for type="question"
    outcome: Literal["success", "partial", "failed"] | None = None  # for type="result"
    details: dict[str, str] | None = None   # e.g. {"time": "21:00", "name": "Sina"}
    transcript: list[dict] | None = None    # [{"speaker": "agent"|"business", "text": "..."}]
