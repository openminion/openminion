from typing import Any, Mapping

from openminion.modules.storage.record_store import RecordStore

from .json_utils import parse_json

_CLOSED_TASK_STATUSES = {"done", "completed", "cancelled", "closed", "failed"}


class SessionSliceQueries:
    def __init__(
        self,
        record_store: RecordStore,
        *,
        lock: Any,
    ) -> None:
        self._record_store = record_store
        self._lock = lock

    def _first_row(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> dict[str, Any] | None:
        rows = self._record_store.query_dicts(sql, params)
        return rows[0] if rows else None

    def latest_event_seq_tx(self, session_id: str) -> int:
        row = self._first_row(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_events WHERE session_id = ?",
            (session_id,),
        )
        return int(row["max_seq"]) if row is not None else 0

    def derive_open_tasks(
        self, *, session_id: str, upto_seq: int | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if upto_seq is not None:
            clauses.append("seq <= ?")
            params.append(max(0, int(upto_seq)))
        query = f"""
            SELECT seq, event_type, task_id, payload_json
            FROM session_events
            WHERE {" AND ".join(clauses)}
              AND (
                event_type LIKE 'task.%'
                OR event_type LIKE 'job.%'
              )
            ORDER BY seq ASC
        """
        with self._lock:
            rows = self._record_store.query_dicts(query, tuple(params))

        by_task: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = parse_json(row["payload_json"], {})
            event_type = str(row["event_type"])
            task_id = row["task_id"]
            if task_id is None:
                task_id = payload.get("task_id") or payload.get("job_id")
            if task_id is None:
                continue
            task_key = str(task_id)
            item = by_task.setdefault(
                task_key,
                {
                    "task_id": task_key,
                    "title": str(
                        payload.get("title") or payload.get("method") or task_key
                    ),
                    "status": str(payload.get("status") or "open"),
                    "last_seq": int(row["seq"]),
                    "note": payload.get("note"),
                },
            )

            if payload.get("title"):
                item["title"] = str(payload["title"])
            if payload.get("note") is not None:
                item["note"] = payload.get("note")
            item["last_seq"] = int(row["seq"])

            if event_type == "task.opened":
                item["status"] = str(payload.get("status") or "open")
            elif event_type == "task.updated":
                item["status"] = str(
                    payload.get("status") or item.get("status") or "open"
                )
            elif event_type == "job.created":
                item["status"] = str(payload.get("status") or "queued")
            elif event_type == "job.started":
                item["status"] = str(payload.get("status") or "running")
            elif event_type == "job.completed":
                item["status"] = str(payload.get("status") or "completed")
            elif event_type == "job.cancelled":
                item["status"] = str(payload.get("status") or "cancelled")

        open_items = [
            item
            for item in by_task.values()
            if str(item.get("status", "")).lower() not in _CLOSED_TASK_STATUSES
        ]
        open_items.sort(
            key=lambda item: (
                int(item.get("last_seq", 0)),
                str(item.get("task_id", "")),
            )
        )
        for item in open_items:
            item.pop("last_seq", None)
        return open_items

    def list_recent_archive_ref_lines(
        self, *, session_id: str, limit: int
    ) -> list[str]:
        safe_limit = max(1, int(limit))

        def _to_line(payload: Mapping[str, Any]) -> str | None:
            rel = str(payload.get("relative_path") or payload.get("path") or "").strip()
            if not rel:
                return None
            first_rowid = payload.get("first_rowid")
            last_rowid = payload.get("last_rowid")
            msg_count = payload.get("message_count")
            if (
                first_rowid is not None
                and last_rowid is not None
                and msg_count is not None
            ):
                return f"{rel} (rowid={first_rowid}-{last_rowid}, messages={msg_count})"
            return rel

        with self._lock:
            session_rows = self._record_store.query_dicts(
                """
                SELECT payload_json
                FROM session_events
                WHERE session_id = ?
                  AND event_type = 'session.compaction.archive'
                ORDER BY seq DESC
                LIMIT ?
                """,
                (session_id, safe_limit),
            )

        merged_desc: list[str] = []
        seen: set[str] = set()
        for row in session_rows:
            payload = parse_json(str(row["payload_json"]), {})
            if not isinstance(payload, dict):
                continue
            line = _to_line(payload)
            if not line or line in seen:
                continue
            seen.add(line)
            merged_desc.append(line)
            if len(merged_desc) >= safe_limit:
                break

        merged_desc.reverse()
        return merged_desc
