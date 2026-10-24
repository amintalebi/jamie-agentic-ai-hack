"""Memory smoke test: check the Qdrant connection and exercise the store end to end.

    python scripts/qdrant_test.py

Reads QDRANT_URL and QDRANT_API_KEY from .env. With QDRANT_URL empty it runs against an
embedded in-memory Qdrant instead, so this also works offline.
Pass --wipe to delete the jamie_* collections first (useful after changing the seed data).
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.contracts import CallBrief, CallEvent  # noqa: E402
from server import memory, settings  # noqa: E402  (loads .env)

TEST_USER = "+4917600005678"


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wipe", action="store_true", help="drop the jamie_* collections first")
    args = p.parse_args()

    store = memory.QdrantMemoryStore()
    where = settings.QDRANT_URL or settings.QDRANT_PATH or "embedded in-memory"
    print(f"target: {where}")
    print(f"api key: {'set' if settings.QDRANT_API_KEY else 'not set'}")

    client = await store._ensure_ready()
    if args.wipe:
        for collection in (
            memory.FACTS_COLLECTION,
            memory.MESSAGES_COLLECTION,
            memory.CALLS_COLLECTION,
            memory.BUSINESSES_COLLECTION,
        ):
            await client.delete_collection(collection)
        print("collections dropped, recreating")
        store._ready = False
        store._seeded_numbers = None
        client = await store._ensure_ready()

    collections = await client.get_collections()
    print("collections:", sorted(c.name for c in collections.collections))

    facts = await store.get_facts(TEST_USER)
    print("seeded facts:", facts)

    await store.remember(TEST_USER, "seating_preference", "inside")
    print("after remember:", await store.get_facts(TEST_USER))

    await store.log_message(TEST_USER, "user", "book a table for four tomorrow at eight")
    await store.log_message(TEST_USER, "assistant", "Inside or outside?")
    print("recent messages:", await store.recent_messages(TEST_USER, limit=5))

    hits = await store.find_business("italian")
    print("find_business('italian'):", [(b["name"], b["phone"]) for b in hits])

    brief = CallBrief(
        call_id="smoketest",
        user_id=TEST_USER,
        to_number=hits[0]["phone"],
        language="de",
        caller_name=facts.get("name", "Sina"),
        goal="Book a table for 4, tomorrow, 20:00, inside",
        user_facts=facts,
        allowed_decisions=["time within 30 min of 20:00"],
        must_ask_user_for=["any deposit or fee"],
    )
    await store.save_call(
        brief,
        CallEvent(
            call_id="smoketest",
            type="result",
            outcome="success",
            text="Table for 4 booked at 21:00",
            details={"time": "21:00", "persons": "4"},
        ),
    )
    count = await client.count(memory.CALLS_COLLECTION)
    print("calls stored:", count.count)
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
