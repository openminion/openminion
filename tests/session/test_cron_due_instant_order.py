"""Stored cron due times are compared as instants, not as text.

Every instant here is a fixed, aware value written in the offset it is meant to
arrive in, so no assertion depends on the host's local zone or on the clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.task.scheduling.schedule import to_iso_utc

UTC = timezone.utc
MINUS_FIVE = timezone(timedelta(hours=-5))
PLUS_NINE = timezone(timedelta(hours=9))
PLUS_FOURTEEN = timezone(timedelta(hours=14))

# The poll instant for every boundary assertion: 12:00:00.100000Z.
POLL = datetime(2030, 11, 7, 12, 0, 0, 100000, tzinfo=UTC)
ONE_MICROSECOND = timedelta(microseconds=1)
# Far enough away that add_cron_job never schedules these jobs itself.
NOT_YET = "2036-01-01T00:00:00+00:00"


def _iso(moment: datetime) -> str:
    """The instant written in *moment*'s own offset, fraction included."""
    return moment.isoformat()


def _store(tmp_path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(tmp_path / "cron-due.db")


def _add_far_future_job(store: SQLiteSessionStore, *, name: str) -> str:
    return store.add_cron_job(
        name=name,
        schedule={"kind": "at", "at": NOT_YET},
        payload={"kind": "systemEvent", "event_text": name},
    )


def _seed_raw_due(store: SQLiteSessionStore, job_id: str, due: str) -> None:
    """Store *due* exactly as given, past the store's own canonicalisation."""
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?", (due, job_id)
    )
    store._conn.commit()


def _poll(
    store: SQLiteSessionStore, at: datetime, *, max_jobs: int = 10
) -> list[dict[str, Any]]:
    return store.enqueue_due_cron_runs(
        daemon_id="daemon-1", now_iso=_iso(at), max_jobs=max_jobs
    )


def test_due_rows_are_ordered_by_instant_across_offsets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        # Both due inside the default lateness window, and written as an earlier
        # version of the store may have left them. A text comparison orders
        # "+09:00" ('20:59:58') after UTC ('11:59:59'), the reverse of the instants.
        earlier = datetime(2030, 11, 7, 11, 59, 58, 100000, tzinfo=UTC)
        later = datetime(2030, 11, 7, 11, 59, 59, 100000, tzinfo=UTC)
        earlier_id = _add_far_future_job(store, name="earlier")
        later_id = _add_far_future_job(store, name="later")
        _seed_raw_due(store, earlier_id, _iso(earlier.astimezone(PLUS_NINE)))
        _seed_raw_due(store, later_id, _iso(later))

        queued = _poll(store, POLL, max_jobs=1)
        assert [run["job_id"] for run in queued] == [earlier_id]
        assert queued[0]["due_at"] == to_iso_utc(earlier)
        assert _iso(earlier.astimezone(PLUS_NINE)) > _iso(later)  # text order differs

        remaining = _poll(store, POLL, max_jobs=1)
        assert [run["job_id"] for run in remaining] == [later_id]
    finally:
        store.close()


def test_a_job_due_one_microsecond_after_the_poll_is_not_selected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        due = POLL + ONE_MICROSECOND
        job_id = _add_far_future_job(store, name="one-microsecond-later")
        _seed_raw_due(store, job_id, _iso(due))

        assert _poll(store, POLL) == []
        stored = store.get_cron_job(job_id)
        assert stored is not None
        assert stored["next_due_at"] == _iso(due)
        assert stored["enabled"] is True

        # The same job must still run, at its own instant: the boundary only moves
        # selection, it does not hide the job.
        queued = _poll(store, due)
        assert [run["job_id"] for run in queued] == [job_id]
        assert queued[0]["due_at"] == _iso(due)
    finally:
        store.close()


def test_a_due_time_in_another_offset_survives_a_restart(tmp_path: Path) -> None:
    due = datetime(2030, 11, 7, 15, 0, 0, tzinfo=UTC)  # written as 10:00-05:00
    store = _store(tmp_path)
    try:
        job_id = _add_far_future_job(store, name="offset-restart")
        _seed_raw_due(store, job_id, _iso(due.astimezone(MINUS_FIVE)))
    finally:
        store.close()

    reopened = _store(tmp_path)
    try:
        assert _poll(reopened, POLL) == []
        stored = reopened.get_cron_job(job_id)
        assert stored is not None
        assert stored["next_due_at"] == _iso(due.astimezone(MINUS_FIVE))

        queued = _poll(reopened, due)
        assert [run["job_id"] for run in queued] == [job_id]
        assert queued[0]["due_at"] == to_iso_utc(due)
    finally:
        reopened.close()


def test_a_compensation_write_stores_a_restored_offset_as_canonical_utc(
    tmp_path: Path,
) -> None:
    due = datetime(2030, 11, 7, 15, 0, 0, tzinfo=UTC)
    capped = datetime(2030, 11, 7, 16, 0, 0, 200000, tzinfo=UTC)
    store = _store(tmp_path)
    try:
        restored_id = _add_far_future_job(store, name="restored-offset")
        canonical_id = _add_far_future_job(store, name="restored-canonical")
        store.set_cron_job_enabled(restored_id, enabled=False)
        store.set_cron_job_enabled(
            restored_id,
            enabled=True,
            next_due_at_override=_iso(due.astimezone(PLUS_FOURTEEN)),
        )
        store.set_cron_job_enabled(canonical_id, enabled=False)
        store.set_cron_job_enabled(
            canonical_id, enabled=True, next_due_at_override=to_iso_utc(capped)
        )

        restored = store.get_cron_job(restored_id)
        assert restored is not None
        assert restored["next_due_at"] == to_iso_utc(due)
        assert str(restored["next_due_at"]).endswith("+00:00")
        # Control: an override that is already canonical is stored verbatim, so the
        # compensation write cannot shift an instant it did not have to rewrite.
        canonical = store.get_cron_job(canonical_id)
        assert canonical is not None
        assert canonical["next_due_at"] == to_iso_utc(capped)

        assert _poll(store, POLL) == []
        queued = _poll(store, due)
        assert [run["job_id"] for run in queued] == [restored_id]
        assert queued[0]["due_at"] == to_iso_utc(due)
        assert _poll(store, capped)[0]["job_id"] == canonical_id
    finally:
        store.close()


def test_an_idle_poll_reads_one_limited_cron_jobs_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    try:
        for index in range(12):
            _add_far_future_job(store, name=f"future-{index}")

        reads: list[tuple[str, tuple[Any, ...]]] = []
        query_dicts = store._record_store.query_dicts

        def _recording_query_dicts(
            sql: str, params: Any = None
        ) -> list[dict[str, Any]]:
            if "FROM cron_jobs" in sql:
                reads.append((sql, tuple(params or ())))
            return query_dicts(sql, params)

        monkeypatch.setattr(store._record_store, "query_dicts", _recording_query_dicts)

        assert _poll(store, POLL, max_jobs=7) == []
        # The due-time read is the boundary this pins: one query, limited to the
        # caller's row budget. It makes no claim about a fallback scan.
        assert len(reads) == 1
        sql, params = reads[0]
        assert "LIMIT ?" in sql
        assert params[-1] == 7
    finally:
        store.close()


def test_an_unreadable_due_time_is_never_due(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        unreadable = {
            "text": "next tuesday-ish",
            "calendar": "2030-02-30T00:00:00+00:00",
            "year": "0000-01-01T00:00:00+00:00",
            "empty": "",
        }
        job_ids = {
            label: _add_far_future_job(store, name=f"unreadable-{label}")
            for label in unreadable
        }
        for label, value in unreadable.items():
            _seed_raw_due(store, job_ids[label], value)

        assert _poll(store, POLL) == []
        for label, value in unreadable.items():
            stored = store.get_cron_job(job_ids[label])
            assert stored is not None
            assert stored["next_due_at"] == value
    finally:
        store.close()


def test_due_sql_placeholders_match_the_bound_parameters() -> None:
    from openminion.modules.session.storage.cron_due import (
        _POSTGRES_DUE_ROWS_SQL,
        _SQLITE_DUE_ROWS_SQL,
    )

    # PostgreSQL's record store rewrites positional '?' placeholders, so a literal
    # '?' in either statement (a regex quantifier, say) breaks that backend.
    assert _SQLITE_DUE_ROWS_SQL.count("?") == 4
    assert _POSTGRES_DUE_ROWS_SQL.count("?") == 2
