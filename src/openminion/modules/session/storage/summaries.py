from typing import Any, Callable
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore

from .json_utils import parse_json, to_json


class StateStore:
    def __init__(
        self,
        record_store: RecordStore,
        *,
        touch_session_tx: Callable[..., None],
        invalidate_slice_cache: Callable[[str], None],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self._rs = record_store
        self._touch_session_tx = touch_session_tx
        self._invalidate_slice_cache = invalidate_slice_cache
        self._utc_now_iso = utc_now_iso

    def _first_row(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(sql, params)
        return rows[0] if rows else None

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int:
        with self._rs.transaction():
            row = self._first_row(
                "SELECT COALESCE(MAX(version), 0) AS max_version FROM working_state WHERE session_id = ?",
                (session_id,),
            )
            next_version = int(row["max_version"]) + 1 if row is not None else 1
            now = self._utc_now_iso()
            self._rs.execute_count(
                """
                INSERT INTO working_state(session_id, version, ts, state_ref, state_inline_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    next_version,
                    now,
                    state_ref,
                    to_json(state_inline) if state_inline is not None else None,
                ),
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._invalidate_slice_cache(session_id)
        return next_version

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None:
        row = self._first_row(
            """
            SELECT session_id, version, ts, state_ref, state_inline_json
            FROM working_state
            WHERE session_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (session_id,),
        )
        if row is None:
            return None
        return {
            "session_id": str(row["session_id"]),
            "version": int(row["version"]),
            "ts": str(row["ts"]),
            "state_ref": row["state_ref"],
            "state_inline": (
                parse_json(str(row["state_inline_json"]), {})
                if row["state_inline_json"] is not None
                else None
            ),
        }

    def get_active_state(self, session_id: str) -> dict[str, Any]:
        snapshot = self._first_row(
            """
            SELECT state_json
            FROM session_snapshots
            WHERE session_id = ?
            ORDER BY seq_upto DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        )
        if snapshot is not None:
            parsed = parse_json(snapshot["state_json"], {})
            if isinstance(parsed, dict):
                return parsed
        latest = self.get_latest_working_state(session_id)
        if latest is None:
            return {}
        inline = latest.get("state_inline")
        if isinstance(inline, dict):
            return inline
        if latest.get("state_ref"):
            return {"state_ref": latest["state_ref"]}
        return {}


class SummaryStore:
    def __init__(
        self,
        record_store: RecordStore,
        *,
        touch_session_tx: Callable[..., None],
        invalidate_slice_cache: Callable[[str], None],
        latest_event_seq_tx: Callable[[str], int],
        derive_open_tasks: Callable[..., list[dict[str, Any]]],
        append_event: Callable[..., str],
        get_latest_working_state: Callable[[str], dict[str, Any] | None],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self._rs = record_store
        self._touch_session_tx = touch_session_tx
        self._invalidate_slice_cache = invalidate_slice_cache
        self._latest_event_seq_tx = latest_event_seq_tx
        self._derive_open_tasks = derive_open_tasks
        self._append_event = append_event
        self._get_latest_working_state = get_latest_working_state
        self._utc_now_iso = utc_now_iso

    def _first_row(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(sql, params)
        return rows[0] if rows else None

    def set_summary_base(self, session_id: str, base_ref: str) -> None:
        now = self._utc_now_iso()
        with self._rs.transaction():
            self._rs.execute_count(
                """
                INSERT INTO summaries(session_id, base_ref, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  base_ref = excluded.base_ref,
                  updated_at = excluded.updated_at
                """,
                (session_id, base_ref, now),
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._invalidate_slice_cache(session_id)

    def append_summary_delta(self, session_id: str, delta_ref: str) -> None:
        with self._rs.transaction():
            row = self._first_row(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM summary_deltas WHERE session_id = ?",
                (session_id,),
            )
            next_seq = int(row["max_seq"]) + 1 if row is not None else 1
            now = self._utc_now_iso()
            self._rs.execute_count(
                """
                INSERT INTO summary_deltas(session_id, seq, delta_ref, ts)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, next_seq, delta_ref, now),
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._invalidate_slice_cache(session_id)

    def get_summaries(self, session_id: str) -> dict[str, Any]:
        base_row = self._first_row(
            "SELECT base_ref FROM summaries WHERE session_id = ?",
            (session_id,),
        )
        delta_rows = self._rs.query_dicts(
            """
            SELECT seq, delta_ref, ts
            FROM summary_deltas
            WHERE session_id = ?
            ORDER BY seq ASC
            """,
            (session_id,),
        )
        return {
            "session_id": session_id,
            "base_ref": base_row["base_ref"] if base_row is not None else None,
            "delta_refs": [str(row["delta_ref"]) for row in delta_rows],
            "delta_items": [
                {
                    "seq": int(row["seq"]),
                    "delta_ref": str(row["delta_ref"]),
                    "ts": str(row["ts"]),
                }
                for row in delta_rows
            ],
        }

    def get_summary(self, session_id: str, *, variant: str = "short") -> str:
        variant_value = str(variant or "short").lower()
        row = self._first_row(
            """
            SELECT summary_short, summary_long
            FROM session_summaries
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if row is None:
            row = self._first_row(
                """
                SELECT summary_short, summary_long
                FROM session_snapshots
                WHERE session_id = ?
                ORDER BY seq_upto DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            )
        if row is None:
            return ""
        short_text = str(row["summary_short"] or "")
        long_text = str(row["summary_long"] or "")
        if variant_value in {"long", "auto"}:
            return long_text or short_text
        return short_text

    def needs_summary_update(
        self, session_id: str, *, threshold_events: int = 40
    ) -> bool:
        threshold = max(1, int(threshold_events))
        latest_seq = self._latest_event_seq_tx(session_id)
        row = self._first_row(
            """
            SELECT based_on_seq
            FROM session_summaries
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if row is None:
            return latest_seq >= threshold
        based_on = int(row["based_on_seq"] or 0)
        return (latest_seq - based_on) >= threshold

    def update_summary(
        self,
        session_id: str,
        summary_short: str,
        *,
        summary_long: str | None = None,
        based_on_seq: int,
    ) -> None:
        now = self._utc_now_iso()
        based_seq_value = max(0, int(based_on_seq))
        with self._rs.transaction():
            self._rs.execute_count(
                """
                INSERT INTO session_summaries(
                  session_id, summary_short, summary_long, updated_at, based_on_seq
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  summary_short = excluded.summary_short,
                  summary_long = excluded.summary_long,
                  updated_at = excluded.updated_at,
                  based_on_seq = excluded.based_on_seq
                """,
                (session_id, summary_short, summary_long, now, based_seq_value),
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._invalidate_slice_cache(session_id)

        self._append_event(
            session_id,
            event_type="summary.updated",
            payload={
                "summary_short": summary_short,
                "summary_long": summary_long,
                "based_on_seq": based_seq_value,
            },
            actor_type="system",
            importance=1,
        )

    def create_snapshot(self, session_id: str, seq_upto: int | None = None) -> str:
        exists = self._rs.query_dicts(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        if not exists:
            raise ValueError(f"session not found: {session_id}")
        latest_seq = self._latest_event_seq_tx(session_id)
        boundary = (
            latest_seq if seq_upto is None else max(0, min(int(seq_upto), latest_seq))
        )

        summary_short = self.get_summary(session_id, variant="short")
        summary_long = self.get_summary(session_id, variant="long")
        latest_state = self._get_latest_working_state(session_id)
        if latest_state is None:
            active_state: dict[str, Any] = {}
        elif isinstance(latest_state.get("state_inline"), dict):
            active_state = dict(latest_state["state_inline"])
        elif latest_state.get("state_ref"):
            active_state = {"state_ref": latest_state["state_ref"]}
        else:
            active_state = {}
        open_tasks = self._derive_open_tasks(session_id=session_id, upto_seq=boundary)

        snapshot_id = uuid4().hex
        now = self._utc_now_iso()
        with self._rs.transaction():
            self._rs.execute_count(
                """
                INSERT INTO session_snapshots(
                  snapshot_id, session_id, seq_upto, summary_short, summary_long,
                  state_json, open_tasks_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    session_id,
                    boundary,
                    summary_short,
                    summary_long,
                    to_json(active_state),
                    to_json(open_tasks),
                    now,
                ),
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._invalidate_slice_cache(session_id)
        return snapshot_id

    def update_derived_views(self, session_id: str) -> dict[str, Any]:
        rows = self._rs.query_dicts(
            "SELECT * FROM session_events WHERE session_id=? ORDER BY seq",
            (session_id,),
        )
        if not rows:
            return {"session_id": session_id, "events_processed": 0}

        open_tasks: list[str] = []
        for row in rows:
            payload = parse_json(str(row["payload_json"]), {})
            event_type = str(row["event_type"])
            if event_type in ("task.created", "task.started"):
                task_desc = payload.get("description") or payload.get("task_id", "")
                if task_desc and task_desc not in open_tasks:
                    open_tasks.append(task_desc)
            if event_type in ("task.completed", "task.cancelled", "task.failed"):
                task_desc = payload.get("description") or payload.get("task_id", "")
                if task_desc in open_tasks:
                    open_tasks.remove(task_desc)

        latest_seq = int(rows[-1]["seq"])
        snapshot_id = str(uuid4())
        now = self._utc_now_iso()
        existing_summary = self.get_summary(session_id)
        summary_short = (
            existing_summary.get("summary_short", "")
            if isinstance(existing_summary, dict)
            else str(existing_summary)
        )

        try:
            self._rs.execute_count(
                """
                INSERT INTO session_snapshots
                  (snapshot_id, session_id, seq_upto, summary_short, state_json,
                   open_tasks_json, created_at)
                VALUES (?, ?, ?, ?, '{}', ?, ?)
                """,
                (
                    snapshot_id,
                    session_id,
                    latest_seq,
                    summary_short,
                    to_json(open_tasks),
                    now,
                ),
            )
        except Exception:
            pass

        return {
            "session_id": session_id,
            "events_processed": len(rows),
            "open_tasks": open_tasks,
            "latest_seq": latest_seq,
        }
