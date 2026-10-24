"""User memory (owner: C): profile facts, message log, call records, businesses.

Backed by Qdrant (AsyncQdrantClient). Runs embedded locally (in-memory or QDRANT_PATH) by default,
or connects to a Qdrant server when QDRANT_URL is set.
InMemoryStore is kept as a lightweight fake when MEMORY_MODE=fake.
"""
import asyncio
import hashlib
import logging
import math
import re
import time
import uuid
from typing import Literal, Protocol

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from common.contracts import CallBrief, CallEvent
from server import settings

log = logging.getLogger("server.memory")

Role = Literal["user", "assistant"]

_UUID_NAMESPACE = uuid.UUID("7f9d4c2a-5b1e-4e8a-9c3d-1a2b3c4d5e6f")
_VECTOR_DIM = 384

FACTS_COLLECTION = "jamie_facts"
MESSAGES_COLLECTION = "jamie_messages"
CALLS_COLLECTION = "jamie_calls"
BUSINESSES_COLLECTION = "jamie_businesses"


def _point_id(key: str) -> str:
    """Deterministic UUID string from an arbitrary key."""
    return str(uuid.uuid5(_UUID_NAMESPACE, key))


def _embed_text(text: str, dim: int = _VECTOR_DIM) -> list[float]:
    """Fast, offline character n-gram + word token feature hashing into an L2-normalized vector."""
    vec = [0.0] * dim
    cleaned = re.sub(r"[^a-z0-9äöüß\s]+", " ", text.lower()).strip()
    if not cleaned:
        vec[0] = 1.0
        return vec

    tokens = cleaned.split()
    features: list[tuple[str, float]] = []
    for tok in tokens:
        features.append((f"w:{tok}", 2.0))
        padded = f"<{tok}>"
        for n in (3, 4):
            for i in range(max(0, len(padded) - n + 1)):
                features.append((f"c{n}:{padded[i:i + n]}", 1.0))

    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "little")
        idx = h % dim
        sign = 1.0 if ((h >> 32) & 1) == 0 else -1.0
        vec[idx] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    else:
        vec[0] = 1.0
    return vec


def _default_facts() -> dict[str, str]:
    """Identity a never-seen user starts with, so the demo works against an empty database."""
    return {"name": settings.DEMO_USER_NAME, "usual_italian": settings.DEMO_BUSINESS_NAME}


def _seed_businesses() -> list[dict[str, str]]:
    numbers = sorted(settings.allowed_call_numbers())
    primary = numbers[0] if numbers else "+490000000000"
    second = numbers[1] if len(numbers) > 1 else primary
    return [
        {
            "name": settings.DEMO_BUSINESS_NAME,
            "kind": "italian restaurant pizza pasta",
            "phone": primary,
            "notes": "Usual Italian place in Berlin, indoor and outdoor tables",
        },
        {
            "name": "Ristorante Roma",
            "kind": "italian restaurant",
            "phone": primary,
            "notes": "Classic Italian dining",
        },
        {
            "name": "Sakura Sushi",
            "kind": "japanese sushi restaurant",
            "phone": second,
            "notes": "Sushi and ramen",
        },
    ]


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
        self.businesses = _seed_businesses()

    async def get_facts(self, user_id: str) -> dict[str, str]:
        return self.facts.setdefault(user_id, _default_facts())

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
        hits = [
            b for b in self.businesses
            if any(w in (b["name"] + " " + b["kind"]).lower() for w in q.split())
        ]
        return hits or self.businesses


class QdrantMemoryStore:
    """Qdrant-backed memory store supporting both embedded local mode and remote QDRANT_URL."""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        path: str | None = None,
    ) -> None:
        self._url = url if url is not None else settings.QDRANT_URL
        self._api_key = api_key if api_key is not None else settings.QDRANT_API_KEY
        self._path = path if path is not None else settings.QDRANT_PATH
        self._client: AsyncQdrantClient | None = None
        self._init_lock = asyncio.Lock()
        self._ready = False
        self._seq_counter = 0
        self._seeded_numbers: tuple[str, ...] | None = None

    def _create_client(self) -> AsyncQdrantClient:
        if self._url:
            log.info("connecting to Qdrant server at %s", self._url)
            return AsyncQdrantClient(url=self._url, api_key=self._api_key or None, timeout=10)
        if self._path:
            log.info("opening local Qdrant storage at %s", self._path)
            return AsyncQdrantClient(path=self._path)
        log.info("opening embedded in-memory Qdrant instance")
        return AsyncQdrantClient(location=":memory:")

    async def _ensure_ready(self) -> AsyncQdrantClient:
        if self._ready and self._client is not None:
            return self._client
        async with self._init_lock:
            if self._ready and self._client is not None:
                return self._client
            client = self._create_client()
            for collection in (
                FACTS_COLLECTION,
                MESSAGES_COLLECTION,
                CALLS_COLLECTION,
                BUSINESSES_COLLECTION,
            ):
                exists = await client.collection_exists(collection)
                if not exists:
                    await client.create_collection(
                        collection_name=collection,
                        vectors_config=VectorParams(size=_VECTOR_DIM, distance=Distance.COSINE),
                    )
            # A real Qdrant server rejects payload filters on unindexed fields; embedded mode
            # does not, so these must exist before the first scroll against a cluster.
            for collection in (FACTS_COLLECTION, MESSAGES_COLLECTION, CALLS_COLLECTION):
                try:
                    await client.create_payload_index(
                        collection_name=collection,
                        field_name="user_id",
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
                except Exception as exc:  # already indexed, or local mode where it is a no-op
                    log.debug("payload index on %s.user_id skipped: %s", collection, exc)
            self._client = client
            await self._seed_default_businesses(client)
            self._ready = True
            return client

    async def _seed_default_businesses(self, client: AsyncQdrantClient) -> None:
        """Upsert the demo businesses. Skipped when ALLOWED_CALL_NUMBERS has not changed,
        so a remote cluster is not hit on every find_business call."""
        numbers = tuple(sorted(settings.allowed_call_numbers()))
        if self._seeded_numbers == numbers:
            return
        points = []
        for biz in _seed_businesses():
            doc = f"{biz['name']} {biz['kind']} {biz.get('notes', '')}"
            points.append(
                PointStruct(
                    id=_point_id(f"biz:{biz['name'].lower()}"),
                    vector=_embed_text(doc),
                    payload=biz,
                )
            )
        if points:
            await client.upsert(collection_name=BUSINESSES_COLLECTION, points=points)
        self._seeded_numbers = numbers

    async def _upsert_fact_point(
        self, client: AsyncQdrantClient, user_id: str, key: str, value: str
    ) -> None:
        point_id = _point_id(f"fact:{user_id}:{key}")
        vector = _embed_text(f"{key}: {value}")
        payload = {
            "user_id": user_id,
            "key": key,
            "value": value,
            "updated_at": time.time(),
        }
        await client.upsert(
            collection_name=FACTS_COLLECTION,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    async def get_facts(self, user_id: str) -> dict[str, str]:
        client = await self._ensure_ready()
        records, _ = await client.scroll(
            collection_name=FACTS_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=200,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            defaults = _default_facts()
            for k, v in defaults.items():
                await self._upsert_fact_point(client, user_id, k, v)
            return dict(defaults)

        facts: dict[str, str] = {}
        for rec in records:
            payload = rec.payload or {}
            k = payload.get("key")
            v = payload.get("value")
            if k is not None and v is not None:
                facts[str(k)] = str(v)
        return facts

    async def remember(self, user_id: str, key: str, value: str) -> None:
        client = await self._ensure_ready()
        # Ensure defaults exist first if this is a brand-new user
        await self.get_facts(user_id)
        await self._upsert_fact_point(client, user_id, key, value)

    async def log_message(self, user_id: str, role: Role, text: str) -> None:
        client = await self._ensure_ready()
        self._seq_counter += 1
        now = time.time()
        seq = int(now * 1_000_000) + self._seq_counter
        point_id = str(uuid.uuid4())
        payload = {
            "user_id": user_id,
            "role": role,
            "text": text,
            "seq": seq,
            "created_at": now,
        }
        await client.upsert(
            collection_name=MESSAGES_COLLECTION,
            points=[PointStruct(id=point_id, vector=_embed_text(text), payload=payload)],
        )

    async def recent_messages(self, user_id: str, limit: int = 20) -> list[dict]:
        client = await self._ensure_ready()
        records, _ = await client.scroll(
            collection_name=MESSAGES_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=500,
            with_payload=True,
            with_vectors=False,
        )
        items = [r.payload for r in records if r.payload]
        items.sort(key=lambda p: (p.get("seq", 0), p.get("created_at", 0.0)))
        tail = items[-limit:] if limit > 0 else items
        return [{"role": p["role"], "text": p["text"]} for p in tail]

    async def save_call(self, brief: CallBrief, result: CallEvent) -> None:
        client = await self._ensure_ready()
        call_id = brief.call_id or result.call_id or uuid.uuid4().hex[:12]
        point_id = _point_id(f"call:{call_id}")
        summary_text = f"{brief.goal} -> {result.outcome or result.type}: {result.text}"
        payload = {
            "call_id": call_id,
            "user_id": brief.user_id,
            "brief": brief.model_dump(),
            "result": result.model_dump(),
            "created_at": time.time(),
        }
        await client.upsert(
            collection_name=CALLS_COLLECTION,
            points=[PointStruct(id=point_id, vector=_embed_text(summary_text), payload=payload)],
        )

    async def find_business(self, query: str) -> list[dict]:
        client = await self._ensure_ready()
        # Keep businesses synced with the current ALLOWED_CALL_NUMBERS on each search
        await self._seed_default_businesses(client)

        records, _ = await client.scroll(
            collection_name=BUSINESSES_COLLECTION,
            limit=100,
            with_payload=True,
            with_vectors=False,
        )
        all_businesses = [dict(r.payload) for r in records if r.payload]
        q = query.strip().lower()
        if not q:
            return all_businesses

        # 1. Exact/substring token match first for deterministic accuracy
        words = [w for w in re.split(r"\W+", q) if len(w) >= 2]
        token_hits = [
            b for b in all_businesses
            if any(
                w in f"{b.get('name', '')} {b.get('kind', '')} {b.get('notes', '')}".lower()
                for w in words
            )
        ]
        if token_hits:
            return token_hits

        # 2. Qdrant vector similarity search fallback
        query_res = await client.query_points(
            collection_name=BUSINESSES_COLLECTION,
            query=_embed_text(q),
            limit=5,
            with_payload=True,
        )
        scored = [dict(pt.payload) for pt in query_res.points if pt.payload and (pt.score or 0.0) > 0.1]
        return scored or all_businesses

    async def upsert_business(self, name: str, kind: str, phone: str, notes: str = "") -> None:
        client = await self._ensure_ready()
        biz = {"name": name, "kind": kind, "phone": phone, "notes": notes}
        doc = f"{name} {kind} {notes}"
        await client.upsert(
            collection_name=BUSINESSES_COLLECTION,
            points=[
                PointStruct(
                    id=_point_id(f"biz:{name.lower()}"),
                    vector=_embed_text(doc),
                    payload=biz,
                )
            ],
        )


store: MemoryStore = (
    InMemoryStore() if settings.MEMORY_MODE == "fake" else QdrantMemoryStore()
)


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


async def upsert_business(name: str, kind: str, phone: str, notes: str = "") -> None:
    if isinstance(store, QdrantMemoryStore):
        await store.upsert_business(name, kind, phone, notes)
    elif isinstance(store, InMemoryStore):
        store.businesses.append({"name": name, "kind": kind, "phone": phone, "notes": notes})
