"""User memory (owner: C): profile facts, message log, call records, businesses.

Real store: TODO(C) SQLite or Postgres (DATABASE_URL), team to decide. The fake keeps
everything in process and seeds the demo data so the flow works without a database.
"""
import logging
from typing import Literal, Protocol

from common.contracts import CallBrief, CallEvent
from server import settings

log = logging.getLogger("server.memory")

Role = Literal["user", "assistant"]


class MemoryStore(Protocol):
    async def get_facts(self, user_id: str) -> dict[str, str]: ...
    async def remember(self, user_id: str, key: str, value: str) -> None: ...
    async def log_message(self, user_id: str, role: Role, text: str) -> None: ...
    async def recent_messages(self, user_id: str, limit: int = 20) -> list[dict]: ...
    async def save_call(self, brief: CallBrief, result: CallEvent) -> None: ...
    async def find_business(self, query: str) -> list[dict]: ...


class InMemoryStore:
    def __init__(self) -> None:
        self.facts: dict[str, dict[str, str]] = {}
        self.messages: dict[str, list[dict]] = {}
        self.calls: list[dict] = []
        demo_number = next(iter(sorted(settings.allowed_call_numbers())), "+490000000000")
        self.businesses = [
            {"name": "Trattoria da Demo", "kind": "italian restaurant", "phone": demo_number},
        ]

    async def get_facts(self, user_id: str) -> dict[str, str]:
        # TODO(hack): every new user starts as the demo user Sina.
        return self.facts.setdefault(user_id, {"name": "Sina", "usual_italian": "Trattoria da Demo"})

    async def remember(self, user_id: str, key: str, value: str) -> None:
        (await self.get_facts(user_id))[key] = value

    async def log_message(self, user_id: str, role: Role, text: str) -> None:
        self.messages.setdefault(user_id, []).append({"role": role, "text": text})

    async def recent_messages(self, user_id: str, limit: int = 20) -> list[dict]:
        return self.messages.get(user_id, [])[-limit:]

    async def save_call(self, brief: CallBrief, result: CallEvent) -> None:
        self.calls.append({"brief": brief.model_dump(), "result": result.model_dump()})

    async def find_business(self, query: str) -> list[dict]:
        q = query.lower()
        hits = [b for b in self.businesses if any(w in (b["name"] + " " + b["kind"]).lower() for w in q.split())]
        return hits or self.businesses


store: MemoryStore = InMemoryStore()


async def get_facts(user_id: str) -> dict[str, str]:
    return await store.get_facts(user_id)


async def remember(user_id: str, key: str, value: str) -> None:
    await store.remember(user_id, key, value)


async def log_message(user_id: str, role: Role, text: str) -> None:
    await store.log_message(user_id, role, text)


async def recent_messages(user_id: str, limit: int = 20) -> list[dict]:
    return await store.recent_messages(user_id, limit)


async def save_call(brief: CallBrief, result: CallEvent) -> None:
    await store.save_call(brief, result)


async def find_business(query: str) -> list[dict]:
    return await store.find_business(query)
