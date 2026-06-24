import datetime

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.models import MemoryRecord


def test_retrieve_by_entities(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test.db")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    records = [
        MemoryRecord(
            id="r1",
            scope="session:1",
            type="fact",
            content="Alice in Wonderland",
            created_at=now,
            updated_at=now,
            entities=["Alice", "Wonderland"],
        ),
        MemoryRecord(
            id="r2",
            scope="session:1",
            type="fact",
            content="Alice and Bob",
            created_at=now,
            updated_at=now,
            entities=["Alice", "Bob"],
        ),
        MemoryRecord(
            id="r3",
            scope="session:2",
            type="fact",
            content="Wonderland theme park",
            created_at=now,
            updated_at=now,
            entities=["Wonderland"],
        ),
    ]

    for record in records:
        store.put(record)

    res_alice = store.retrieve_by_entities(["Alice"], scopes=["session:1"])
    assert len(res_alice) == 2
    assert {record.id for record in res_alice} == {"r1", "r2"}

    res_wonderland = store.retrieve_by_entities(
        ["Wonderland"], scopes=["session:1", "session:2"]
    )
    assert len(res_wonderland) == 2
    assert {record.id for record in res_wonderland} == {"r1", "r3"}

    store.upsert(
        "session:1",
        "fact",
        "story",
        {"content": "Bob in Wonderland", "entities": ["Bob", "Wonderland"]},
    )

    res_upsert = store.retrieve_by_entities(["Bob"], scopes=["session:1"])
    assert len(res_upsert) == 2
