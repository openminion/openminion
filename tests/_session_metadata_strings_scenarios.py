"""Session metadata string row scenarios shared by the SQLite and PostgreSQL tests.

pytest does not rewrite the assertions of this module, so each check compares
one result with its expectation and prints both on failure.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Sequence
from typing import Any
from unittest.mock import patch

from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    Migration,
    MigrationRows,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.session_store import metadata_strings
from openminion.modules.storage.runtime.session_store.metadata_strings import (
    backfill_session_metadata_strings,
    metadata_string_rows,
)
from openminion.modules.storage.runtime.session_store.rows import (
    metadata_json,
    parse_json_object,
)
from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnFenceError,
)

NUL = chr(0)
NAN = float("nan")
INF = float("inf")
LONE_HIGH = chr(0xD800)
LONE_LOW = chr(0xDC00)
CAFE = "caf" + chr(0xE9)
EMOJI = chr(0x1F600)
LONG_KEY = "k" * 5_000
LONG_VALUE = "v" * 100_000

_SESSIONS_MODULE = "openminion.modules.storage.runtime.session_store.sessions"
_TARGET = "metadata-strings"
LEGACY_TARGET = "legacy-metadata-strings"

# Metadata that PostgreSQL cannot parse as a whole document, or that a
# filter must read literally. Session id -> metadata.
ODD_METADATA: dict[str, dict[Any, Any]] = {
    "odd-nan-other": {"surface": "acp", "score": NAN},
    "odd-inf-other": {"surface": "acp", "low": -INF, "high": INF},
    "odd-nul-other": {"surface": "acp", "note": "a" + NUL + "b"},
    "odd-surrogate-other": {"surface": "acp", "note": LONE_HIGH + "x" + LONE_LOW},
    "odd-nul-value": {"surface": "a" + NUL + "b"},
    "odd-nul-key": {"k" + NUL + "z": "value"},
    "odd-surrogate-value": {"surface": "x" + LONE_HIGH},
    "odd-unicode": {CAFE: EMOJI, "surface": CAFE},
    "odd-brace-value": {"surface": '{"a":1}', "working_dir": "[draft]"},
    "odd-object-value": {"surface": {"a": 1}, "working_dir": ["draft"]},
    "odd-path-keys": {"a.b": "literal", "a": {"b": "nested"}, "t[0]": "x", '"q"': "y"},
    "odd-long": {LONG_KEY: LONG_VALUE, "surface": "acp"},
    "odd-nan-keys": {NAN: "first", float("nan"): "second"},
    "odd-non-strings": {"surface": 1, "flag": True, "none": None, "n": 1.5},
    "odd-empty-string": {"surface": ""},
}

# Filter -> the sessions of ODD_METADATA it selects on PostgreSQL.
POSTGRES_ODD_EXPECTED: list[tuple[dict[str, str], set[str]]] = [
    (
        {"surface": "acp"},
        {
            "odd-nan-other",
            "odd-inf-other",
            "odd-nul-other",
            "odd-surrogate-other",
            "odd-long",
        },
    ),
    ({"surface": "a" + NUL + "b"}, {"odd-nul-value"}),
    ({"k" + NUL + "z": "value"}, {"odd-nul-key"}),
    ({"surface": "x" + LONE_HIGH}, {"odd-surrogate-value"}),
    ({"note": LONE_HIGH + "x" + LONE_LOW}, {"odd-surrogate-other"}),
    ({CAFE: EMOJI}, {"odd-unicode"}),
    ({"surface": CAFE}, {"odd-unicode"}),
    ({"surface": '{"a":1}'}, {"odd-brace-value"}),
    ({"working_dir": "[draft]"}, {"odd-brace-value"}),
    ({"working_dir": '["draft"]'}, set()),
    ({"a.b": "literal"}, {"odd-path-keys"}),
    ({"a.b": "nested"}, set()),
    ({"t[0]": "x"}, {"odd-path-keys"}),
    ({'"q"': "y"}, {"odd-path-keys"}),
    ({LONG_KEY: LONG_VALUE}, {"odd-long"}),
    ({LONG_KEY: LONG_VALUE[:-1]}, set()),
    # json.loads keeps the last of two members with one name.
    ({"NaN": "second"}, {"odd-nan-keys"}),
    ({"NaN": "first"}, set()),
    ({"surface": "1"}, set()),
    ({"flag": "true"}, set()),
    ({"none": ""}, set()),
    ({"surface": ""}, {"odd-empty-string"}),
    ({"surface": "acp", "note": "a" + NUL + "b"}, {"odd-nul-other"}),
]


def stored_rows(sessions: SessionStore) -> set[tuple[str, str, str, str]]:
    rows = sessions._backend.query_dicts(
        "SELECT session_id, key, value, entry_digest FROM session_metadata_strings"
    )
    found = [
        (str(r["session_id"]), str(r["key"]), str(r["value"]), str(r["entry_digest"]))
        for r in rows
    ]
    assert len(found) == len(set(found)), ("duplicate rows", found)
    return set(found)


def derived_rows(sessions: SessionStore) -> set[tuple[str, str, str, str]]:
    """The rows derived from every stored session's `metadata_json`."""
    expected = set()
    for row in sessions._backend.query_dicts("SELECT id, metadata_json FROM sessions"):
        for fact in metadata_string_rows(str(row["id"]), str(row["metadata_json"])):
            expected.add(
                (fact["session_id"], fact["key"], fact["value"], fact["entry_digest"])
            )
    return expected


def assert_rows_match_metadata(sessions: SessionStore) -> None:
    actual, expected = stored_rows(sessions), derived_rows(sessions)
    assert actual == expected, (
        sorted(actual ^ expected)[:10],
        len(actual),
        len(expected),
    )


def stored_metadata(sessions: SessionStore) -> dict[str, str]:
    rows = sessions._backend.query_dicts("SELECT id, metadata_json FROM sessions")
    return {str(row["id"]): str(row["metadata_json"]) for row in rows}


def _store(sessions: SessionStore, metadata_by_id: dict[str, dict[Any, Any]]) -> None:
    for session_id, metadata in metadata_by_id.items():
        sessions.resolve_session(
            agent_id="main",
            channel="console",
            target=_TARGET,
            session_id=session_id,
            metadata=metadata,
        )


def _selected(
    sessions: SessionStore, metadata_filter: dict[str, Any], limit: int = 100
) -> set[str]:
    rows = sessions.list_sessions(
        limit=limit, target=_TARGET, metadata_filter=metadata_filter
    )
    return {row.id for row in rows}


def reference_ids(
    sessions: SessionStore,
    metadata_filter: dict[str, str],
    *,
    limit: int,
    newest_first: bool,
    target: str = _TARGET,
) -> list[str]:
    """The ids the PostgreSQL contract selects, read from the stored rows in Python."""
    filters = [
        (str(key).strip(), value or "")
        for key, value in metadata_filter.items()
        if str(key or "").strip()
    ]
    rows = sessions._backend.query_dicts(
        "SELECT id, updated_at, metadata_json FROM sessions WHERE target = ?",
        (target,),
    )
    selected = []
    for row in rows:
        metadata = parse_json_object(str(row["metadata_json"]))
        if all(
            isinstance(metadata.get(key), str) and metadata[key] == value
            for key, value in filters
        ):
            selected.append((str(row["updated_at"]), str(row["id"])))
    selected.sort(reverse=newest_first)
    return [session_id for _, session_id in selected[:limit]]


def check_filters_against_reference(sessions: SessionStore, target: str) -> None:
    """PostgreSQL: each `POSTGRES_ODD_EXPECTED` filter returns the reference ids."""
    for metadata_filter, _ in POSTGRES_ODD_EXPECTED:
        for limit in (3, 100000):
            rows = sessions.list_sessions(
                limit=limit, target=target, metadata_filter=metadata_filter
            )
            actual = [row.id for row in rows]
            expected = reference_ids(
                sessions, metadata_filter, limit=limit, newest_first=True, target=target
            )
            assert actual == expected, (metadata_filter, limit, actual, expected)


def check_odd_metadata_rows(sessions: SessionStore) -> None:
    """Writes of metadata PostgreSQL cannot parse keep exact rows (both backends)."""
    _store(sessions, ODD_METADATA)
    assert_rows_match_metadata(sessions)
    rows = stored_rows(sessions)
    keys = {key for _, key, _, _ in rows}
    # Names and values are stored as their JSON text: ASCII, no NUL byte.
    assert all(text.isascii() and NUL not in text for row in rows for text in row), rows
    assert metadata_strings.encode_member("k" + NUL + "z") in keys, keys
    assert metadata_strings.encode_member(LONG_KEY) in keys, len(keys)


def check_postgres_literal_contract(sessions: SessionStore) -> None:
    """PostgreSQL: a literal top-level string match on every stored document."""
    _store(sessions, ODD_METADATA)
    actual = [(item, _selected(sessions, item)) for item, _ in POSTGRES_ODD_EXPECTED]
    assert actual == POSTGRES_ODD_EXPECTED, [
        (a, e) for a, e in zip(actual, POSTGRES_ODD_EXPECTED) if a != e
    ]


def check_bounded_results(sessions: SessionStore) -> None:
    """At most `limit` sessions, in the store's order, among unparseable documents."""
    for index in range(24):
        metadata: dict[Any, Any] = {"surface": "acp", "index": index}
        if index % 3 == 0:
            metadata["score"] = NAN
        if index % 4 == 0:
            metadata["note"] = "a" + NUL + "b"
        _store(sessions, {f"bounded-{index:02d}": metadata})
    for index in range(30):
        _store(sessions, {f"other-{index:02d}": {"surface": "cli", "score": INF}})
    for limit in (1, 2, 5, 23, 24, 25, 1000):
        for newest_first in (True, False):
            rows = sessions.list_sessions(
                limit=limit,
                newest_first=newest_first,
                target=_TARGET,
                metadata_filter={"surface": "acp"},
            )
            actual = [row.id for row in rows]
            expected = reference_ids(
                sessions, {"surface": "acp"}, limit=limit, newest_first=newest_first
            )
            assert actual == expected, (limit, newest_first, actual, expected)
            assert len(actual) == min(limit, 24), (limit, len(actual))


def check_forced_digest_collision(sessions: SessionStore) -> None:
    """Rows that share one digest still compare their full name and value."""
    with patch.object(metadata_strings, "entry_digest", lambda key, value: "0" * 16):
        _store(sessions, ODD_METADATA)
        _store(sessions, {"near": {"surface": "acq", "a.b": "literals"}})
        digests = {digest for _, _, _, digest in stored_rows(sessions)}
        assert digests == {"0" * 16}, digests
        actual = [
            (item, _selected(sessions, item)) for item, _ in POSTGRES_ODD_EXPECTED
        ]
        assert actual == POSTGRES_ODD_EXPECTED, [
            (a, e) for a, e in zip(actual, POSTGRES_ODD_EXPECTED) if a != e
        ]
        near = _selected(sessions, {"surface": "acq"})
        assert near == {"near"}, near


def check_write_paths(sessions: SessionStore) -> None:
    """Every session write leaves the rows equal to the stored metadata."""
    created = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target=_TARGET,
        metadata={"surface": "acp", "count": 1},
    )
    assert_rows_match_metadata(sessions)
    # The existing session, with new metadata: replaced as a whole.
    sessions.resolve_session(
        agent_id="main",
        channel="console",
        target=_TARGET,
        metadata={"surface": "cli", "working_dir": "/work/a"},
    )
    assert_rows_match_metadata(sessions)
    # Without metadata the row and its strings stay.
    before = stored_rows(sessions)
    sessions.resolve_session(agent_id="main", channel="console", target=_TARGET)
    assert stored_rows(sessions) == before
    explicit = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target=_TARGET,
        session_id="explicit-a",
        metadata={"surface": "acp"},
    )
    room = sessions.create_room(
        channel="cli", target="room", session_id="room-a", metadata={"topic": CAFE}
    )
    assert_rows_match_metadata(sessions)
    # An insert that conflicts with an existing session writes nothing.
    before = stored_rows(sessions)
    sessions._sessions.insert_session(
        session_id=explicit.id,
        session_key="other-key",
        channel="console",
        target=_TARGET,
        session_metadata_json=metadata_json({"surface": "replaced"}),
        created_at="2026-10-26T00:00:00+00:00",
        updated_at="2026-10-26T00:00:00+00:00",
        ignore_conflict=True,
    )
    assert stored_rows(sessions) == before
    kept = sessions.get_session(explicit.id)
    assert kept is not None and kept.metadata == {"surface": "acp"}, kept
    # A changed value, a string turned into a number, a new member.
    sessions.update_session_metadata(
        session_id=created.id,
        patch={"surface": "focus", "working_dir": 7, "title": "t"},
    )
    assert_rows_match_metadata(sessions)
    assert sessions.delete_session(room.id)
    assert_rows_match_metadata(sessions)
    remaining = {session_id for session_id, _, _, _ in stored_rows(sessions)}
    assert room.id not in remaining, remaining
    assert {created.id, explicit.id} <= remaining, remaining


def _failing_after(name: str) -> Callable[..., None]:
    original = getattr(metadata_strings, name)

    def write_then_fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected failure after the metadata strings")

    return write_then_fail


def check_failed_writes_roll_back(sessions: SessionStore) -> None:
    """A write that fails after its rows leaves the session and rows unchanged."""
    session = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target=_TARGET,
        session_id="rollback-a",
        metadata={"surface": "acp"},
    )
    metadata_before, rows_before = stored_metadata(sessions), stored_rows(sessions)
    failing_replace = _failing_after("replace_metadata_strings")
    failing_insert = _failing_after("insert_metadata_strings")
    attempts: list[Callable[[], object]] = [
        lambda: sessions.update_session_metadata(
            session_id=session.id, patch={"surface": "cli"}
        ),
    ]
    for attempt in attempts:
        with patch(f"{_SESSIONS_MODULE}.replace_metadata_strings", failing_replace):
            try:
                attempt()
            except RuntimeError as exc:
                assert "injected failure" in str(exc), exc
            else:
                raise AssertionError("the injected failure did not raise")
        assert stored_metadata(sessions) == metadata_before
        assert stored_rows(sessions) == rows_before
    # The keyed session's metadata update in resolve_session.
    keyed = sessions.resolve_session(
        agent_id="main", channel="console", target=_TARGET, metadata={"surface": "a"}
    )
    metadata_before, rows_before = stored_metadata(sessions), stored_rows(sessions)
    with patch(f"{_SESSIONS_MODULE}.replace_metadata_strings", failing_replace):
        try:
            sessions.resolve_session(
                agent_id="main",
                channel="console",
                target=_TARGET,
                metadata={"surface": "b"},
            )
        except RuntimeError as exc:
            assert "injected failure" in str(exc), exc
        else:
            raise AssertionError("the injected failure did not raise")
    assert stored_metadata(sessions) == metadata_before
    assert stored_rows(sessions) == rows_before
    kept = sessions.get_session(keyed.id)
    assert kept is not None and kept.metadata == {"surface": "a"}, kept
    # A new session whose rows fail is not stored either.
    with patch(f"{_SESSIONS_MODULE}.insert_metadata_strings", failing_insert):
        try:
            sessions.create_room(
                channel="cli", target="room", session_id="room-b", metadata={"x": "y"}
            )
        except RuntimeError as exc:
            assert "injected failure" in str(exc), exc
        else:
            raise AssertionError("the injected failure did not raise")
    assert sessions.get_session("room-b") is None
    assert stored_metadata(sessions) == metadata_before
    assert stored_rows(sessions) == rows_before
    # A fence check that fails comes before any write, as before.
    try:
        sessions.update_session_metadata(
            session_id=session.id, patch={"surface": "x"}, session_turn_fence_token=1
        )
    except RuntimeSessionTurnFenceError:
        pass
    else:
        raise AssertionError("the fence check did not raise")
    assert stored_metadata(sessions) == metadata_before
    assert stored_rows(sessions) == rows_before


def check_session_delete_cascade(sessions: SessionStore) -> None:
    """Deleting a session row removes its rows, through the store or directly."""
    _store(sessions, {"del-a": {"surface": "acp"}, "del-b": {"surface": "cli"}})
    assert sessions.delete_session("del-a")
    sessions._backend.execute_count("DELETE FROM sessions WHERE id = ?", ("del-b",))
    remaining = {session_id for session_id, _, _, _ in stored_rows(sessions)}
    assert not remaining & {"del-a", "del-b"}, remaining


def check_concurrent_metadata_writers(
    sessions: SessionStore, writers: Sequence[SessionStore], *, rounds: int = 25
) -> None:
    """Concurrent writers to one session leave rows matching the final metadata."""
    session = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target=_TARGET,
        session_id="concurrent-a",
        metadata={"surface": "start"},
    )
    barrier = threading.Barrier(len(writers))
    errors: list[BaseException] = []

    def write(index: int, store: SessionStore) -> None:
        try:
            barrier.wait()
            for step in range(rounds):
                patch_value: dict[str, Any] = {
                    "surface": f"writer-{index}-{step}",
                    f"writer-{index}": "x" if step % 2 else 1,
                }
                store.update_session_metadata(session_id=session.id, patch=patch_value)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=(index, store))
        for index, store in enumerate(writers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert_rows_match_metadata(sessions)
    final = sessions.get_session(session.id)
    assert final is not None
    listed = _selected(sessions, {"surface": final.metadata["surface"]})
    assert listed == {session.id}, (listed, final.metadata)


def check_concurrent_inserts_keep_the_first_rows(
    sessions: SessionStore, writers: Sequence[SessionStore]
) -> None:
    """Racing creations of one keyed session: the rows match its stored metadata."""
    barrier = threading.Barrier(len(writers))
    errors: list[BaseException] = []

    def create(index: int, store: SessionStore) -> None:
        try:
            barrier.wait()
            store.resolve_session(
                agent_id="main",
                channel="console",
                target="raced-target",
                metadata={"surface": f"creator-{index}"},
            )
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=create, args=(index, store))
        for index, store in enumerate(writers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert_rows_match_metadata(sessions)


def check_update_racing_delete(
    sessions: SessionStore, updater: SessionStore, deleter: SessionStore
) -> None:
    """A metadata write and a delete of one session wait for each other."""
    session = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target=_TARGET,
        session_id="race-delete",
        metadata={"surface": "start"},
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def update() -> None:
        try:
            barrier.wait()
            for step in range(40):
                try:
                    updater.update_session_metadata(
                        session_id=session.id, patch={"surface": f"s{step}"}
                    )
                except (ValueError, RuntimeError) as exc:
                    # Raised as before when the delete commits first: before
                    # the read, or between the read and the update.
                    if "Session not found" not in str(exc) and (
                        "Failed to update metadata" not in str(exc)
                    ):
                        raise
                    return
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    def delete() -> None:
        try:
            barrier.wait()
            deleter.delete_session(session.id)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=update), threading.Thread(target=delete)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert sessions.get_session(session.id) is None
    remaining = {session_id for session_id, _, _, _ in stored_rows(sessions)}
    assert session.id not in remaining, remaining
    assert_rows_match_metadata(sessions)


# Runtime migration 15 on existing history.


def legacy_session_rows(count: int) -> list[dict[str, Any]]:
    """`count` sessions as stored before runtime migration 15, plus `legacy-long`.

    `metadata_json` is written by the store's writer, with members PostgreSQL
    cannot parse, long and non-ASCII members, and non-string values.
    """
    odd = [metadata for name, metadata in ODD_METADATA.items() if name != "odd-long"]
    rows = []
    for index in range(count):
        metadata: dict[Any, Any] = dict(odd[index % len(odd)])
        if all(isinstance(key, str) for key in metadata):
            metadata["index"] = index
            metadata["label"] = f"session {index}"
        rows.append(
            {
                "id": f"legacy-{index:05d}",
                "session_key": f"legacy-key-{index:05d}",
                "channel": "console",
                "target": LEGACY_TARGET,
                "metadata_json": metadata_json(metadata),
                "created_at": "2026-10-25T00:00:00+00:00",
                "updated_at": f"2026-10-25T{index // 3600 % 24:02d}:"
                f"{index // 60 % 60:02d}:{index % 60:02d}+00:00",
            }
        )
    rows.append(
        {
            "id": "legacy-long",
            "session_key": "legacy-key-long",
            "channel": "console",
            "target": LEGACY_TARGET,
            "metadata_json": metadata_json(ODD_METADATA["odd-long"]),
            "created_at": "2026-10-25T00:00:00+00:00",
            "updated_at": "2026-10-25T23:59:59+00:00",
        }
    )
    return rows


def migrations_before(version: int) -> tuple[Migration, ...]:
    return tuple(item for item in DEFAULT_MIGRATIONS if item.version < version)


def recorded_metadata_backfill_migrations(batches: list[int]) -> tuple[Migration, ...]:
    """`DEFAULT_MIGRATIONS` whose migration 15 backfill records each batch it reads."""

    class Recorder:
        def __init__(self, rows: MigrationRows) -> None:
            self._rows = rows

        def query_dicts(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
            found = self._rows.query_dicts(sql, params)
            batches.append(len(found))
            return found

        def insert_many(self, table: str, rows: list[dict[str, Any]]) -> int:
            return self._rows.insert_many(table, rows)

    def backfill(rows: MigrationRows) -> None:
        backfill_session_metadata_strings(Recorder(rows))

    return tuple(
        dataclasses.replace(item, backfill=backfill) if item.version == 15 else item
        for item in DEFAULT_MIGRATIONS
    )


def failing_extraction(session_id: str) -> Callable[[str, str], list[dict[str, Any]]]:
    """`metadata_string_rows` that raises a non-JSON error for one session."""
    original = metadata_strings.metadata_string_rows

    def rows_or_fail(current: str, stored: str) -> list[dict[str, Any]]:
        if current == session_id:
            raise RuntimeError(f"extraction failed for {current}")
        return original(current, stored)

    return rows_or_fail
