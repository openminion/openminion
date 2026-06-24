from __future__ import annotations

import datetime
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def test_fts_tokenizer_underscore(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    store.put(
        MemoryRecord(
            id="underscore-1",
            scope="session:underscore",
            type="fact",
            content="foo_bar_baz unique token",
            created_at=now,
            updated_at=now,
        )
    )

    results = store.search(
        SearchQueryOptions(
            query="foo_bar_baz",
            scopes=["session:underscore"],
        )
    )
    assert any(item.id == "underscore-1" for item in results)
