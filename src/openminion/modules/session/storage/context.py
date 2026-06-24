from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore

from .json_utils import parse_json, to_json


def _first_row(
    record_store: RecordStore,
    sql: str,
    params: tuple[object, ...],
) -> dict[str, Any] | None:
    rows = record_store.query_dicts(sql, params)
    return rows[0] if rows else None


def _meta_json(row: dict[str, Any]) -> dict[str, Any]:
    return parse_json(str(row["meta_json"]), {})


class ContextStore:
    def __init__(
        self,
        record_store: RecordStore,
        *,
        utc_now_iso: Callable[[], str],
    ) -> None:
        self._rs = record_store
        self._utc_now_iso = utc_now_iso

    def create_prompt_context(
        self,
        session_id: str,
        *,
        seed_bundle_id: str | None = None,
        checkpoint_id: str | None = None,
        prefix_hash: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        with self._rs.transaction():
            pc_id = str(uuid4())
            now = self._utc_now_iso()
            self._rs.execute_count(
                """
                UPDATE prompt_contexts SET status='closed', closed_at=?
                WHERE session_id=? AND status='active'
                """,
                (now, session_id),
            )
            self._rs.execute_count(
                """
                INSERT INTO prompt_contexts
                  (prompt_context_id, session_id, created_at, status,
                   seed_bundle_id, checkpoint_id, prefix_hash, meta_json)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    pc_id,
                    session_id,
                    now,
                    seed_bundle_id,
                    checkpoint_id,
                    prefix_hash,
                    to_json(meta or {}),
                ),
            )
            return pc_id

    def close_prompt_context(
        self,
        prompt_context_id: str,
        *,
        rollover_reason: str | None = None,
    ) -> None:
        now = self._utc_now_iso()
        self._rs.execute_count(
            """
            UPDATE prompt_contexts
            SET status='closed', closed_at=?, rollover_reason=?
            WHERE prompt_context_id=?
            """,
            (now, rollover_reason, prompt_context_id),
        )

    def get_active_prompt_context(self, session_id: str) -> dict[str, Any] | None:
        row = _first_row(
            self._rs,
            """
            SELECT * FROM prompt_contexts
            WHERE session_id=? AND status='active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        )
        if row is None:
            return None
        return {
            "prompt_context_id": str(row["prompt_context_id"]),
            "session_id": str(row["session_id"]),
            "created_at": str(row["created_at"]),
            "closed_at": row["closed_at"],
            "status": str(row["status"]),
            "seed_bundle_id": row["seed_bundle_id"],
            "checkpoint_id": row["checkpoint_id"],
            "prefix_hash": row["prefix_hash"],
            "rollover_reason": row["rollover_reason"],
            "meta": _meta_json(row),
        }

    def save_compression_checkpoint(
        self,
        session_id: str,
        bundle_json: str,
        *,
        up_to_event_id: str | None = None,
        reason: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        cp_id = str(uuid4())
        now = self._utc_now_iso()
        self._rs.execute_count(
            """
            INSERT INTO compression_checkpoints
              (checkpoint_id, session_id, bundle_json, up_to_event_id,
               created_at, reason, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cp_id,
                session_id,
                bundle_json,
                up_to_event_id,
                now,
                reason,
                to_json(meta or {}),
            ),
        )
        return cp_id

    def get_latest_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        row = _first_row(
            self._rs,
            """
            SELECT * FROM compression_checkpoints
            WHERE session_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        )
        if row is None:
            return None
        return {
            "checkpoint_id": str(row["checkpoint_id"]),
            "session_id": str(row["session_id"]),
            "bundle_json": str(row["bundle_json"]),
            "up_to_event_id": row["up_to_event_id"],
            "created_at": str(row["created_at"]),
            "reason": row["reason"],
            "meta": _meta_json(row),
        }

    def save_seed_bundle(
        self,
        session_id: str,
        source_bundle_id: str,
        sections_json: str,
        total_tokens: int,
        *,
        source_checkpoint_id: str | None = None,
        budgets_json: str = "{}",
        up_to_event_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        seed_id = str(uuid4())
        now = self._utc_now_iso()
        self._rs.execute_count(
            """
            INSERT INTO seed_bundles
              (seed_id, session_id, source_bundle_id, source_checkpoint_id,
               sections_json, total_tokens, budgets_json, up_to_event_id,
               created_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seed_id,
                session_id,
                source_bundle_id,
                source_checkpoint_id,
                sections_json,
                total_tokens,
                budgets_json,
                up_to_event_id,
                now,
                to_json(meta or {}),
            ),
        )
        return seed_id

    def get_latest_seed_bundle(self, session_id: str) -> dict[str, Any] | None:
        row = _first_row(
            self._rs,
            """
            SELECT * FROM seed_bundles
            WHERE session_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        )
        if row is None:
            return None
        return {
            "seed_id": str(row["seed_id"]),
            "session_id": str(row["session_id"]),
            "source_bundle_id": str(row["source_bundle_id"]),
            "source_checkpoint_id": row["source_checkpoint_id"],
            "sections": parse_json(str(row["sections_json"]), []),
            "total_tokens": int(row["total_tokens"]),
            "budgets": parse_json(str(row["budgets_json"]), {}),
            "up_to_event_id": row["up_to_event_id"],
            "created_at": str(row["created_at"]),
            "meta": _meta_json(row),
        }


class RunStore:
    def __init__(
        self,
        record_store: RecordStore,
        *,
        utc_now_iso: Callable[[], str],
    ) -> None:
        self._rs = record_store
        self._utc_now_iso = utc_now_iso

    @staticmethod
    def _normalize_run_record(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["meta"] = parse_json(str(row.get("meta_json", "")), {})
        return normalized

    def create_run_record(
        self,
        session_id: str,
        run_type: str = "llm",
        *,
        run_id: str | None = None,
        prompt_context_id: str | None = None,
        model_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        run_id_value = str(run_id or "").strip() or str(uuid4())
        now = self._utc_now_iso()
        self._rs.execute_count(
            """
            INSERT INTO run_records
              (run_id, session_id, prompt_context_id, run_type, status,
               started_at, model_id, meta_json)
            VALUES (?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id_value,
                session_id,
                prompt_context_id,
                run_type,
                now,
                model_id,
                to_json(meta or {}),
            ),
        )
        return run_id_value

    def finish_run_record(
        self,
        run_id: str,
        *,
        status: str = "completed",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        now = self._utc_now_iso()
        self._rs.execute_count(
            """
            UPDATE run_records
            SET status=?,
                finished_at=?,
                input_tokens=COALESCE(?, input_tokens),
                output_tokens=COALESCE(?, output_tokens)
            WHERE run_id=?
            """,
            (status, now, input_tokens, output_tokens, run_id),
        )

    def add_run_usage_delta(
        self,
        run_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._rs.execute_count(
            """
            UPDATE run_records
            SET input_tokens = COALESCE(input_tokens, 0) + ?,
                output_tokens = COALESCE(output_tokens, 0) + ?
            WHERE run_id = ?
            """,
            (max(0, int(input_tokens)), max(0, int(output_tokens)), run_id),
        )

    def get_run_record(self, run_id: str) -> dict[str, Any] | None:
        rows = self._rs.query_dicts(
            "SELECT * FROM run_records WHERE run_id = ?",
            (run_id,),
        )
        return self._normalize_run_record(dict(rows[0])) if rows else None

    def list_run_records(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._rs.query_dicts(
            """
            SELECT * FROM run_records
            WHERE session_id = ?
            ORDER BY started_at ASC, run_id ASC
            """,
            (session_id,),
        )
        return [self._normalize_run_record(dict(row)) for row in rows]

    def add_message_ref(
        self,
        session_id: str,
        role: str,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        content_ref: str | None = None,
        content_inline: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        with self._rs.transaction():
            ref_id = str(uuid4())
            now = self._utc_now_iso()
            row = _first_row(
                self._rs,
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM message_refs WHERE session_id=?",
                (session_id,),
            )
            next_seq = int(row["next_seq"]) if row else 0
            self._rs.execute_count(
                """
                INSERT INTO message_refs
                  (ref_id, session_id, run_id, event_id, role,
                   content_ref, content_inline, seq, created_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    session_id,
                    run_id,
                    event_id,
                    role,
                    content_ref,
                    content_inline,
                    next_seq,
                    now,
                    to_json(meta or {}),
                ),
            )
            return ref_id
