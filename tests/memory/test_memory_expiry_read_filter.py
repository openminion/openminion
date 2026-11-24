"""Expired records are excluded on read, not only by the purge run."""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.memory.storage.base import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.storage.sqlite import search as sqlite_search
from openminion.modules.memory.storage.sqlite import store as sqlite_store
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore

SCOPE = "agent:expiry"
QUERY_INSTANT = datetime.datetime(
    2026, 11, 6, 16, 0, 0, 100000, tzinfo=datetime.timezone.utc
)


def _iso(delta_days: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=delta_days)
    ).isoformat()


def _offset_iso(delta_hours: int, *, offset_hours: int) -> str:
    zone = datetime.timezone(datetime.timedelta(hours=offset_hours))
    return (
        (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=delta_hours)
        )
        .astimezone(zone)
        .isoformat()
    )


def _store(tmp_path: Path) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(tmp_path / "memory.db")


def _fact(
    store: SQLiteMemoryStore,
    key: str,
    *,
    entities: list[str],
    expires_at: str | None = None,
):
    patch = {
        "title": f"Row {key}",
        "content": {"text": "The staging cluster runs in Frankfurt."},
        "entities": entities,
    }
    if expires_at is not None:
        patch["expires_at"] = expires_at
    return store.upsert(SCOPE, "fact", key, patch)


def _force_expiry(store: SQLiteMemoryStore, record_id: str, value: str) -> None:
    """Store a raw value, as a row a caller-supplied expiry can hold."""
    with store._connect() as conn:
        conn.execute(
            "UPDATE memory_records SET expires_at = ? WHERE id = ?", (value, record_id)
        )


def _stored_expiry(store: SQLiteMemoryStore, record_id: str) -> str | None:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT expires_at FROM memory_records WHERE id = ?", (record_id,)
        ).fetchone()
    return None if row is None else row[0]


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime.datetime) -> None:
    """Pin the query instant the read predicates compare stored expiry against."""

    class _FixedClock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is not None else moment.replace(tzinfo=None)

    clock = SimpleNamespace(
        datetime=_FixedClock, timezone=datetime.timezone, timedelta=datetime.timedelta
    )
    monkeypatch.setattr(sqlite_search, "datetime", clock)
    monkeypatch.setattr(sqlite_store, "datetime", clock)


def test_expired_record_is_not_retrieved_by_entity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(
        SCOPE,
        "fact",
        "fact:expired",
        {
            "title": "Old database",
            "content": {"text": "The primary database is MySQL 5.7."},
            "entities": ["mysql"],
            "expires_at": _iso(-3),
        },
    )

    assert store.retrieve_by_entities(["mysql"], scopes=[SCOPE]) == []


def test_expired_record_is_not_returned_by_search(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(
        SCOPE,
        "fact",
        "fact:expired",
        {
            "title": "Old database",
            "content": {"text": "The primary database is MySQL 5.7."},
            "entities": ["mysql"],
            "expires_at": _iso(-3),
        },
    )

    assert (
        store.search(SearchQueryOptions(query="MySQL", scopes=[SCOPE], limit=5)) == []
    )


def test_unexpired_record_is_still_returned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(
        SCOPE,
        "fact",
        "fact:current",
        {
            "title": "Current database",
            "content": {"text": "The primary database is PostgreSQL 16."},
            "entities": ["postgres"],
            "expires_at": _iso(30),
        },
    )

    by_entity = store.retrieve_by_entities(["postgres"], scopes=[SCOPE])
    assert [record.title for record in by_entity] == ["Current database"]
    hits = store.search(SearchQueryOptions(query="PostgreSQL", scopes=[SCOPE], limit=5))
    assert [record.title for record in hits] == ["Current database"]


def test_record_without_expiry_is_still_returned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(
        SCOPE,
        "fact",
        "fact:permanent",
        {
            "title": "Permanent note",
            "content": {"text": "The team uses trunk-based development."},
            "entities": ["workflow"],
        },
    )

    assert len(store.retrieve_by_entities(["workflow"], scopes=[SCOPE])) == 1


def test_invalidated_history_still_reaches_expired_records(tmp_path: Path) -> None:
    """A history query asks for invalidated rows on purpose; expiry must not hide it."""
    store = _store(tmp_path)
    store.upsert(
        SCOPE,
        "fact",
        "fact:expired",
        {
            "title": "Old database",
            "content": {"text": "The primary database is MySQL 5.7."},
            "entities": ["mysql"],
            "expires_at": _iso(-3),
        },
    )

    found = store.search(
        SearchQueryOptions(
            query="MySQL", scopes=[SCOPE], include_invalidated=True, limit=5
        )
    )

    assert [record.title for record in found] == ["Old database"]


def test_a_future_offset_expiry_stays_readable(tmp_path: Path) -> None:
    """A legacy row holding a future instant in -12:00 must stay readable."""
    store = _store(tmp_path)
    created = _fact(store, "offset-future", entities=["frankfurt"])
    _force_expiry(store, created.id, _offset_iso(6, offset_hours=-12))

    hits = store.search(SearchQueryOptions(query="Frankfurt", scopes=[SCOPE]))
    assert [hit.id for hit in hits] == [created.id]
    by_entity = store.retrieve_by_entities(["frankfurt"], scopes=[SCOPE])
    assert [record.id for record in by_entity] == [created.id]


def test_an_expired_offset_expiry_is_excluded(tmp_path: Path) -> None:
    """A legacy row holding an expired instant in +14:00 must be excluded."""
    store = _store(tmp_path)
    created = _fact(store, "offset-past", entities=["frankfurt"])
    _force_expiry(store, created.id, _offset_iso(-6, offset_hours=14))

    assert store.search(SearchQueryOptions(query="Frankfurt", scopes=[SCOPE])) == []
    assert store.retrieve_by_entities(["frankfurt"], scopes=[SCOPE]) == []


def test_an_expiry_later_in_the_same_second_is_still_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whole-second truncation would hide a row that expires 800ms after the query."""
    store = _store(tmp_path)
    created = _fact(store, "same-second", entities=["frankfurt"])
    _force_expiry(store, created.id, "2026-11-06T16:00:00.900000+00:00")
    _freeze(monkeypatch, QUERY_INSTANT)

    hits = store.search(SearchQueryOptions(query="Frankfurt", scopes=[SCOPE]))
    assert [hit.id for hit in hits] == [created.id]
    by_entity = store.retrieve_by_entities(["frankfurt"], scopes=[SCOPE])
    assert [record.id for record in by_entity] == [created.id]
    assert [r.id for r in store.list(ListQueryOptions(scopes=[SCOPE]))] == [created.id]

    _freeze(monkeypatch, QUERY_INSTANT + datetime.timedelta(seconds=1))
    assert store.search(SearchQueryOptions(query="Frankfurt", scopes=[SCOPE])) == []
    assert store.retrieve_by_entities(["frankfurt"], scopes=[SCOPE]) == []
    assert store.list(ListQueryOptions(scopes=[SCOPE])) == []


def _visible_ids(store: SQLiteMemoryStore) -> set[str]:
    """Ids a default read returns; all three read paths share one expiry predicate."""
    hits = store.search(SearchQueryOptions(query="Frankfurt", scopes=[SCOPE]))
    by_entity = store.retrieve_by_entities(["frankfurt"], scopes=[SCOPE])
    listed = store.list(ListQueryOptions(scopes=[SCOPE]))
    assert {hit.id for hit in hits} == {record.id for record in by_entity}
    assert {hit.id for hit in hits} == {record.id for record in listed}
    return {hit.id for hit in hits}


def test_equivalent_spellings_of_one_instant_compare_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One moment, three spellings: no spelling may decide readability."""
    store = _store(tmp_path)
    fraction = _fact(store, "fraction-six", entities=["frankfurt"])
    short = _fact(store, "fraction-one", entities=["frankfurt"])
    offset = _fact(store, "offset-two", entities=["frankfurt"])
    _force_expiry(store, fraction.id, "2026-11-06T16:00:00.100000+00:00")
    _force_expiry(store, short.id, "2026-11-06T16:00:00.1+00:00")
    _force_expiry(store, offset.id, "2026-11-06T18:00:00.1+02:00")

    _freeze(monkeypatch, QUERY_INSTANT)
    assert _visible_ids(store) == set()

    _freeze(monkeypatch, QUERY_INSTANT - datetime.timedelta(microseconds=1))
    assert _visible_ids(store) == {fraction.id, short.id, offset.id}


def test_a_microsecond_later_expiry_is_readable_until_the_clock_passes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A millisecond floor would hide a row expiring one microsecond after the query."""
    store = _store(tmp_path)
    created = _fact(store, "one-microsecond", entities=["frankfurt"])
    _force_expiry(store, created.id, "2026-11-06T16:00:00.100001+00:00")

    _freeze(monkeypatch, QUERY_INSTANT)
    assert _visible_ids(store) == {created.id}

    _freeze(monkeypatch, QUERY_INSTANT + datetime.timedelta(microseconds=1))
    assert _visible_ids(store) == set()

    _freeze(monkeypatch, QUERY_INSTANT + datetime.timedelta(microseconds=2))
    assert _visible_ids(store) == set()


def test_a_value_without_a_fraction_behaves_as_zero_microseconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    created = _fact(store, "no-fraction", entities=["frankfurt"])
    _force_expiry(store, created.id, "2026-11-06T16:00:00+00:00")
    whole_second = QUERY_INSTANT.replace(microsecond=0)

    _freeze(monkeypatch, whole_second)
    assert _visible_ids(store) == set()

    _freeze(monkeypatch, whole_second - datetime.timedelta(microseconds=1))
    assert _visible_ids(store) == {created.id}


def test_a_plus_fourteen_offset_keeps_its_utc_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``+14:00`` moves the calendar date, not the instant the row expires at."""
    store = _store(tmp_path)
    local = _fact(store, "offset-local", entities=["frankfurt"])
    utc = _fact(store, "offset-utc", entities=["frankfurt"])
    _force_expiry(store, local.id, "2026-11-07T01:00:00.100000+14:00")
    _force_expiry(store, utc.id, "2026-11-06T11:00:00.100000+00:00")

    _freeze(
        monkeypatch, QUERY_INSTANT.replace(hour=11) - datetime.timedelta(microseconds=1)
    )
    assert _visible_ids(store) == {local.id, utc.id}

    _freeze(monkeypatch, QUERY_INSTANT.replace(hour=11))
    assert _visible_ids(store) == set()


def test_an_unreadable_expiry_never_hides_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value that is not a calendar-valid instant is not an expiry."""
    store = _store(tmp_path)
    garbage = _fact(store, "garbage", entities=["frankfurt"])
    impossible = _fact(store, "impossible", entities=["frankfurt"])
    calendar = _fact(store, "calendar", entities=["frankfurt"])
    year_zero = _fact(store, "year-zero", entities=["frankfurt"])
    _force_expiry(store, garbage.id, "!")
    _force_expiry(store, impossible.id, "2026-99-01T00:00:00+00:00")
    _force_expiry(store, calendar.id, "2020-02-30T00:00:00+00:00")
    _force_expiry(store, year_zero.id, "0000-01-01T00:00:00+00:00")
    _freeze(monkeypatch, QUERY_INSTANT)

    expected = {garbage.id, impossible.id, calendar.id, year_zero.id}
    assert _visible_ids(store) == expected


def test_an_unreadable_clock_time_never_hides_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An impossible clock must not be normalised into an instant that hides the row."""
    store = _store(tmp_path)
    hour = _fact(store, "clock-hour", entities=["frankfurt"])
    midnight = _fact(store, "clock-midnight", entities=["frankfurt"])
    minute = _fact(store, "clock-minute", entities=["frankfurt"])
    second = _fact(store, "clock-second", entities=["frankfurt"])
    future = _fact(store, "clock-future", entities=["frankfurt"])
    expired = _fact(store, "clock-expired", entities=["frankfurt"])
    _force_expiry(store, hour.id, "2020-01-01T24:59:00+00:00")
    _force_expiry(store, midnight.id, "2020-01-01T24:00:00+00:00")
    _force_expiry(store, minute.id, "2020-01-01T12:60:00+00:00")
    _force_expiry(store, second.id, "2020-01-01T12:00:60+00:00")
    _force_expiry(store, future.id, "2026-11-06T17:00:00+00:00")
    _force_expiry(store, expired.id, "2026-11-06T15:00:00+00:00")
    _freeze(monkeypatch, QUERY_INSTANT)

    unreadable = {hour.id, midnight.id, minute.id, second.id}
    assert _visible_ids(store) == unreadable | {future.id}

    assert store.get(hour.id) is not None
    assert [record.id for record in store.history(SCOPE, "fact", "clock-hour")] == [
        hour.id
    ]
    history = store.list(ListQueryOptions(scopes=[SCOPE], include_invalidated=True))
    assert unreadable | {future.id, expired.id} <= {record.id for record in history}


def test_limits_count_only_readable_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hidden row must not consume a page: the filter runs inside the query."""
    store = _store(tmp_path)
    live = [_fact(store, f"live-{index}", entities=["frankfurt"]) for index in range(3)]
    expired = [
        _fact(store, f"gone-{index}", entities=["frankfurt"]) for index in range(3)
    ]
    for record in expired:
        _force_expiry(store, record.id, "2026-11-06T15:00:00+00:00")
    _freeze(monkeypatch, QUERY_INSTANT)

    listed = store.list(ListQueryOptions(scopes=[SCOPE], limit=2))
    assert len(listed) == 2
    assert {record.id for record in listed} <= {record.id for record in live}

    hits = store.search(SearchQueryOptions(query="Frankfurt", scopes=[SCOPE], limit=2))
    assert len(hits) == 2
    assert {hit.id for hit in hits} <= {record.id for record in live}

    by_entity = store.retrieve_by_entities(["frankfurt"], scopes=[SCOPE], limit=2)
    assert len(by_entity) == 2
    assert {record.id for record in by_entity} <= {record.id for record in live}


def test_get_and_history_still_reach_an_expired_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expiry filters retrieval; a direct read or a history query still finds the row."""
    store = _store(tmp_path)
    created = _fact(store, "expired-key", entities=["frankfurt"])
    _force_expiry(store, created.id, "2026-11-06T15:00:00+00:00")
    _freeze(monkeypatch, QUERY_INSTANT)

    assert _visible_ids(store) == set()
    assert store.get(created.id) is not None
    assert [record.id for record in store.history(SCOPE, "fact", "expired-key")] == [
        created.id
    ]
    history = store.list(ListQueryOptions(scopes=[SCOPE], include_invalidated=True))
    assert [record.id for record in history] == [created.id]


def test_an_unrelated_upsert_keeps_the_stored_expiry(tmp_path: Path) -> None:
    """Rewriting a legacy value would change a row the caller did not ask to touch."""
    store = _store(tmp_path)
    created = _fact(store, "untouched", entities=["frankfurt"])
    legacy = "2026-11-06T18:00:00+02:00"
    _force_expiry(store, created.id, legacy)

    updated = store.upsert(SCOPE, "fact", "untouched", {"title": "Renamed"})

    assert _stored_expiry(store, created.id) == legacy
    assert _stored_expiry(store, updated.id) == legacy
