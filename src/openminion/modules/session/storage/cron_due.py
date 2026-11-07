"""Due-time selection for cron rows.

The comparison happens in SQL over the stored text, per backend: SQLite compares an
(epoch second, microsecond) pair because its built-in date functions stop at
milliseconds, and PostgreSQL compares native `timestamptz` values behind a shape and
calendar guard so unreadable text never reaches a throwing cast.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from openminion.modules.storage.backends.postgres import RecordStorePostgres
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.task.scheduling.schedule import (
    parse_iso_datetime,
    to_iso_utc,
)


def canonical_due(value: str) -> str:
    """Return *value* as a canonical UTC instant, or unchanged when unparseable."""
    try:
        return to_iso_utc(parse_iso_datetime(value))
    except (ValueError, TypeError):
        # Not a parseable instant: the stored text is left as it is, and the due-time
        # predicates treat it as never due rather than silently rewriting it.
        return value


# SQLite keeps no sub-second part in its built-in date functions that can be
# compared: `julianday` stops at milliseconds, so `...00.100001` polls as due at
# `...00.100000` and a one-shot job is then cleared a microsecond early. The due
# instant is therefore compared as an (epoch second, microsecond) pair: the second
# comes from `strftime('%s', ...)` (cast to INTEGER, because that function returns
# TEXT and a TEXT value sorts above every integer) and the microsecond from the
# stored fraction, scaled up with the fraction absent meaning zero. Equivalent
# spellings of one due instant therefore compare equal: `+14:00` moves the calendar
# date without moving the instant, and `.1` equals `.100000`. An additional guard
# ignores a value that is not a calendar-valid instant from year 0001 on, so an
# unreadable due time is never due instead of being selected and cleared.
_SQLITE_DUE_SECOND = "CAST(strftime('%s', next_due_at) AS INTEGER)"
_SQLITE_DUE_MICROSECOND = (
    "CASE WHEN instr(next_due_at, '.') = 0 THEN 0 "
    "ELSE CAST(round(CAST(substr(next_due_at, instr(next_due_at, '.')) AS REAL)"
    " * 1000000) AS INTEGER) END"
)
_SQLITE_DUE_ROWS_SQL = f"""
        SELECT * FROM cron_jobs
        WHERE enabled = 1 AND next_due_at IS NOT NULL
          AND date(substr(next_due_at, 1, 10)) = substr(next_due_at, 1, 10)
          AND substr(next_due_at, 1, 4) >= '0001'
          AND strftime('%s', next_due_at) IS NOT NULL
          AND (
            {_SQLITE_DUE_SECOND} < ?
            OR (
              {_SQLITE_DUE_SECOND} = ?
              AND {_SQLITE_DUE_MICROSECOND} <= ?
            )
          )
        ORDER BY {_SQLITE_DUE_SECOND} ASC, {_SQLITE_DUE_MICROSECOND} ASC
        LIMIT ?
"""

# PostgreSQL has neither `julianday` nor SQLite's lenient date parsing, so the
# comparison is a native `timestamptz` one, which keeps microseconds. A cast of
# unreadable text raises and would abort the poll, so the cast is reached only
# through a guard that checks the shape, the year and the day within its month
# first; SQLite's predicate treats the same values as not due.
_POSTGRES_DUE_INSTANT = """CASE
            WHEN next_due_at ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])[T ]([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\\.[0-9]{1,6}){0,1}([+-](0[0-9]|1[0-4]):{0,1}[0-5][0-9]|Z){0,1}$'
              AND next_due_at >= '0001'
            THEN CASE
              WHEN CASE
                WHEN substr(next_due_at, 6, 2) IN
                     ('01', '03', '05', '07', '08', '10', '12')
                  THEN substr(next_due_at, 9, 2)::int <= 31
                WHEN substr(next_due_at, 6, 2) IN ('04', '06', '09', '11')
                  THEN substr(next_due_at, 9, 2)::int <= 30
                ELSE substr(next_due_at, 9, 2)::int <= 28 + (CASE
                  WHEN (substr(next_due_at, 1, 4)::int % 4 = 0
                        AND substr(next_due_at, 1, 4)::int % 100 <> 0)
                    OR substr(next_due_at, 1, 4)::int % 400 = 0
                  THEN 1 ELSE 0 END)
              END
              THEN CAST(
                CASE
                  WHEN next_due_at ~ '(Z|[+-][0-9]{2}(:{0,1}[0-9]{2}){0,1})$'
                    THEN next_due_at
                  ELSE next_due_at || '+00:00'
                END AS timestamptz
              )
            END
          END"""
_POSTGRES_DUE_ROWS_SQL = f"""
        SELECT * FROM cron_jobs
        WHERE enabled = 1 AND next_due_at IS NOT NULL
          AND {_POSTGRES_DUE_INSTANT} <= CAST(? AS timestamptz)
        ORDER BY {_POSTGRES_DUE_INSTANT} ASC
        LIMIT ?
"""


def _is_postgres_record_store(record_store: RecordStore) -> bool:
    """Whether *record_store* is the PostgreSQL implementation of the store.

    The dialect follows the object the store was given, as the session backend
    already decides it: a `RecordStorePostgres` is PostgreSQL, and `RecordStore`'s
    other implementation - plus any store that is neither - keeps the SQLite
    predicate. `diagnostics()` also reports the backend, but it runs
    `PRAGMA wal_checkpoint` on SQLite and raises inside a transaction, which is
    where this query runs.
    """
    return isinstance(record_store, RecordStorePostgres)


def due_job_rows(
    record_store: RecordStore, *, now_dt: datetime, limit: int
) -> list[dict[str, Any]]:
    """Rows enabled and due at or before *now_dt*, earliest instant first.

    The comparison and the ordering both happen in SQL over the stored text, so the
    bound value is the same instant the caller polls with rather than a re-parsed
    string. `limit` bounds the rows read, not only the rows returned.
    """
    params: tuple[Any, ...]
    if _is_postgres_record_store(record_store):
        sql = _POSTGRES_DUE_ROWS_SQL
        params = (to_iso_utc(now_dt), limit)
    else:
        second = math.floor(now_dt.timestamp())
        sql = _SQLITE_DUE_ROWS_SQL
        params = (second, second, now_dt.microsecond, limit)
    rows: list[dict[str, Any]] = record_store.query_dicts(sql, params)
    return rows
