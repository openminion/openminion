"""Due-time selection for cron rows.

The comparison happens in SQL over the stored text, per backend: SQLite compares an
(epoch second, microsecond) pair because its built-in date functions stop at
milliseconds, and PostgreSQL compares native `timestamptz` values behind a shape and
calendar guard so unreadable text never reaches a throwing cast.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, cast

from openminion.modules.storage.backends.postgres import RecordStorePostgres
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.task.scheduling.schedule import (
    parse_iso_datetime,
    to_iso_utc,
)


def canonical_due(value: str) -> str:
    """Return *value* as a canonical UTC instant, or unchanged when unparseable."""
    try:
        return cast(str, to_iso_utc(parse_iso_datetime(value)))
    except (ValueError, TypeError):
        # Unparseable text is left as stored; the due-time predicates treat it as
        # never due rather than silently rewriting it.
        return value


# SQLite's date functions stop at milliseconds, so `...00.100001` would poll as due at
# `...00.100000` and a one-shot job would be cleared a microsecond early. The instant
# is therefore compared as an (epoch second, microsecond) pair: the second comes from
# `strftime('%s', ...)` (cast to INTEGER, because that function returns TEXT, which
# sorts above every integer) and the microsecond from the stored fraction, absent
# meaning zero. Equivalent spellings compare equal: `+14:00` moves the calendar date
# without moving the instant, and `.1` equals `.100000`. The guards drop a value that
# is not a calendar-valid instant from year 0001 on, and bound the clock fields to
# what `parse_iso_datetime` accepts: SQLite rolls `24:00:00` into the next day, so
# without the bound a persisted `24:00` would be selected and then raise in the poll.
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
            substr(next_due_at, 11, 1) = ''
            OR (
              substr(next_due_at, 11, 1) IN ('T', ' ')
              AND substr(next_due_at, 12, 2) BETWEEN '00' AND '23'
              AND substr(next_due_at, 14, 1) = ':'
              AND substr(next_due_at, 15, 2) BETWEEN '00' AND '59'
              AND (
                substr(next_due_at, 17, 1) = ''
                OR (
                  substr(next_due_at, 17, 1) = ':'
                  AND substr(next_due_at, 18, 2) BETWEEN '00' AND '59'
                )
              )
            )
          )
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

# PostgreSQL has neither `julianday` nor SQLite's lenient parsing, so the comparison is
# a native `timestamptz` one, which keeps microseconds. Unreadable text would make the
# cast raise and abort the poll, so the cast is reached only behind a shape, year and
# month-day guard; SQLite's predicate treats the same values as not due.
_POSTGRES_DUE_INSTANT = """CASE
            WHEN next_due_at ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])([T ]([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9]){0,1}(\\.[0-9]{1,6}){0,1}){0,1}([Z]|[+-](0[0-9]|1[0-4]):{0,1}[0-5][0-9]){0,1}$'
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
                  WHEN next_due_at ~ '[T ][0-9]{2}:[0-9]{2}'
                    AND next_due_at ~ '(Z|[+-][0-9]{2}(:{0,1}[0-9]{2}){0,1})$'
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


def due_job_rows(
    record_store: RecordStore, *, now_dt: datetime, limit: int
) -> list[dict[str, Any]]:
    """Rows enabled and due at or before *now_dt*, earliest instant first.

    The bound value is the caller's exact instant, and `limit` bounds the rows read,
    not only the rows returned.
    """
    params: tuple[Any, ...]
    # isinstance, not diagnostics(): the latter runs a WAL checkpoint on SQLite, which
    # raises inside the transaction this poll runs in.
    if isinstance(record_store, RecordStorePostgres):
        sql = _POSTGRES_DUE_ROWS_SQL
        params = (to_iso_utc(now_dt), limit)
    else:
        second = math.floor(now_dt.timestamp())
        sql = _SQLITE_DUE_ROWS_SQL
        params = (second, second, now_dt.microsecond, limit)
    return cast(list[dict[str, Any]], record_store.query_dicts(sql, params))
