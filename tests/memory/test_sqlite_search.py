import datetime

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.models import MemoryRecord, RetrievalFilters
from openminion.modules.memory.storage.base import SearchQueryOptions


def _store(tmp_path):
    return SQLiteMemoryStore(tmp_path / "test.db")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def test_search_basic_keyword(tmp_path) -> None:
    store = _store(tmp_path)
    now = _now()
    for record in (
        MemoryRecord(
            id="r1",
            scope="session:1",
            type="fact",
            content="The quick brown fox jumps over the lazy dog",
            created_at=now,
            updated_at=now,
        ),
        MemoryRecord(
            id="r2",
            scope="session:1",
            type="fact",
            content="A fast red fox was seen today",
            created_at=now,
            updated_at=now,
        ),
        MemoryRecord(
            id="r3",
            scope="session:1",
            type="fact",
            content="Nothing to see here about animals.",
            created_at=now,
            updated_at=now,
        ),
    ):
        store.put(record)

    results = store.search(SearchQueryOptions(query="fox", scopes=["session:1"]))
    assert len(results) == 2

    filtered = store.search(
        SearchQueryOptions(
            query="fox",
            scopes=["session:1"],
            filters=RetrievalFilters(scopes=["session:1"], min_confidence=0.9),
        )
    )
    assert len(filtered) == 0


def test_search_ranking(tmp_path) -> None:
    store = _store(tmp_path)
    now = _now()
    for record in (
        MemoryRecord(
            id="r1",
            scope="session:1",
            type="fact",
            content="unique search term",
            created_at=now,
            updated_at=now,
            confidence=0.2,
        ),
        MemoryRecord(
            id="r2",
            scope="session:1",
            type="fact",
            content="unique search term",
            created_at=now,
            updated_at=now,
            confidence=0.9,
        ),
    ):
        store.put(record)

    results = store.search(SearchQueryOptions(query="unique", scopes=["session:1"]))
    assert len(results) == 2
    assert [item.id for item in results] == ["r2", "r1"]


def test_search_colon_query_falls_back_to_sanitized_tokens(tmp_path) -> None:
    store = _store(tmp_path)
    store.put(
        MemoryRecord(
            id="r-colon",
            scope="session:1",
            type="fact",
            content="mdc generalization e2e is active",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    results = store.search(
        SearchQueryOptions(
            query="remember this fact: mdc-generalization-e2e is active",
            scopes=["session:1"],
        )
    )
    assert any(item.id == "r-colon" for item in results)


def test_search_exposes_bm25_scores_in_record_meta(tmp_path) -> None:
    store = _store(tmp_path)
    store.put(
        MemoryRecord(
            id="r-bm25",
            scope="session:1",
            type="fact",
            content="bm25 score probe lexical token",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    first = store.search(SearchQueryOptions(query="lexical", scopes=["session:1"]))[0]
    assert "bm25_raw_score" in first.meta
    assert "bm25_score" in first.meta
    assert 0.0 <= float(first.meta["bm25_score"]) <= 1.0


def test_search_excludes_invalidated_by_default_and_can_include_them(tmp_path) -> None:
    store = _store(tmp_path)
    store.put(
        MemoryRecord(
            id="r-invalidated",
            scope="session:1",
            type="fact",
            content="temporal invalidation target",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    store.invalidate(
        "r-invalidated",
        valid_to="2026-05-21T00:00:00+00:00",
        reason="corrected",
    )
    hidden = store.search(SearchQueryOptions(query="temporal", scopes=["session:1"]))
    assert hidden == []
    visible = store.search(
        SearchQueryOptions(
            query="temporal",
            scopes=["session:1"],
            include_invalidated=True,
        )
    )
    assert [item.id for item in visible] == ["r-invalidated"]
