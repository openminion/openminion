from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _apply_config_overrides(config: Any, overrides: dict[str, Any]) -> Any:
    if not overrides:
        return config
    top_level: dict[str, Any] = {}
    for name, value in overrides.items():
        current = getattr(config, name)
        if isinstance(value, dict) and is_dataclass(current):
            allowed = {field.name for field in fields(current)}
            scoped = {key: item for key, item in value.items() if key in allowed}
            top_level[name] = replace(current, **scoped)
        else:
            top_level[name] = value
    return replace(config, **top_level)


def _shift_iso_timestamp(value: str | None, *, days: int) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed - timedelta(days=int(days))).isoformat()


class E2EMemoryHarness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        agent_id: str,
        project_id: str | None = None,
        store: SQLiteMemoryStore | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.tmp_path = Path(tmp_path)
        self.agent_id = str(agent_id)
        self.project_id = str(project_id or "").strip() or None
        self.store = store or SQLiteMemoryStore(self.tmp_path / f"{self.agent_id}.db")
        self.service = MemoryService(store=self.store)
        base_config = from_base_config(
            base_config=OpenMinionConfig(),
            home_root=self.tmp_path / f"{self.agent_id}-home",
            data_root=self.tmp_path / f"{self.agent_id}-data",
        )
        self.memory_config = _apply_config_overrides(
            base_config,
            config_overrides
            or {
                "reflection": {
                    "reflection_enabled": True,
                    "reflection_interval_sessions": 3,
                },
                "candidate_learning": {"auto_extract_enabled": True},
                "retention": {"gc_enabled": True},
            },
        )
        self.adapter = MemoryServiceGatewayAdapter(
            self.service,
            agent_id=self.agent_id,
            project_id=self.project_id,
            memory_config=self.memory_config,
            trace_enabled=False,
        )
        self._run_counter = 0
        self._request_counter = 0

    def run_turn(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str = "Acknowledged.",
    ) -> Any:
        self._run_counter += 1
        self._request_counter += 1
        return self.adapter.record_turn(
            session_id=session_id,
            run_id=f"run-{self._run_counter}",
            request_id=f"req-{self._request_counter}",
            channel="eval",
            target="user",
            user_message=user_msg,
            assistant_message=assistant_msg,
        )

    def seed_candidate(
        self,
        *,
        session_id: str,
        content: str,
        candidate_type: str = "user_preference",
        title: str | None = None,
        confidence: float = 0.7,
        promotion_ready: bool = True,
        key: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:

        self._run_counter += 1
        base_meta: dict[str, Any] = {}
        if promotion_ready:
            base_meta["reconfirmation_count"] = 3
            base_meta["retrieval_hit_count"] = 3
        if meta:
            base_meta.update(meta)
        candidate_id = f"seed-cand-{self._run_counter}"
        self.service.candidate_put(
            MemoryCandidate(
                candidate_id=candidate_id,
                session_id=session_id,
                proposed_scope=f"agent:{self.agent_id}",
                type=candidate_type,
                title=title or content[:80],
                content=content,
                confidence=confidence,
                key=key,
                meta=base_meta,
            )
        )
        return candidate_id

    def build_capsule(self, session_id: str, user_msg: str) -> str:
        capsule, _meta = self.adapter.build_context_with_metadata(
            session_id=session_id,
            user_message=user_msg,
        )
        return capsule

    def trigger_lifecycle(self, session_id: str = "lifecycle") -> None:
        self.adapter._maybe_run_session_lifecycle(session_id=session_id)  # noqa: SLF001

    def trigger_reflection(self) -> int:
        return self.adapter._maybe_run_reflection()  # noqa: SLF001

    def query_records(
        self,
        *,
        scopes: list[str] | None = None,
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list[Any]:
        return self.service.list(
            ListQueryOptions(
                scopes=scopes or [f"agent:{self.agent_id}"],
                types=types,
                limit=limit,
            )
        )

    def query_candidates(
        self,
        *,
        session_id: str | None = None,
        proposed_scope: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        return self.service.candidate_list(
            CandidateListOptions(
                session_id=session_id,
                proposed_scope=proposed_scope,
                status=status,
                limit=limit,
            )
        )

    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        types: list[str] | None = None,
        limit: int = 20,
    ) -> list[Any]:
        return self.service.search(
            SearchQueryOptions(
                query=query,
                scopes=scopes or [f"agent:{self.agent_id}"],
                types=types,
                limit=limit,
            )
        )

    def seed_summary(
        self,
        *,
        key: str,
        summary_text: str,
        corrections: list[str] | None = None,
        decisions: list[str] | None = None,
        keywords: list[str] | None = None,
        turn_count: int = 3,
        preference_examples: list[dict[str, Any]] | None = None,
    ) -> Any:
        return self.service.upsert_record(
            scope=f"agent:{self.agent_id}",
            record_type="session_summary",
            key=key,
            record_patch={
                "title": key,
                "content": {
                    "decisions": list(decisions or []),
                    "open_questions": [],
                    "corrections": list(corrections or []),
                    "topic_keywords": list(keywords or []),
                    "preference_examples": list(preference_examples or []),
                    "turn_count": turn_count,
                    "summary_text": summary_text,
                },
                "tags": ["session_summary"],
                "entities": list(keywords or []),
                "source": "validated",
                "confidence": 0.8,
            },
        )

    def advance_time(
        self,
        days: int,
        *,
        record_ids: list[str] | None = None,
        candidate_ids: list[str] | None = None,
    ) -> None:
        last_decay = getattr(self.adapter, "_last_decay_run_at", None)
        if last_decay:
            shifted = _shift_iso_timestamp(str(last_decay), days=int(days))
            if shifted:
                self.adapter._last_decay_run_at = shifted  # noqa: SLF001
        with self.store._connect() as conn:  # noqa: SLF001
            if record_ids is None:
                rows = conn.execute(
                    "SELECT id, created_at, updated_at, last_hit_at FROM memory_records"
                ).fetchall()
            else:
                placeholders = ",".join("?" * len(record_ids))
                rows = conn.execute(
                    f"SELECT id, created_at, updated_at, last_hit_at FROM memory_records WHERE id IN ({placeholders})",
                    record_ids,
                )
            for row in rows:
                conn.execute(
                    """
                    UPDATE memory_records
                    SET created_at = ?, updated_at = ?, last_hit_at = ?
                    WHERE id = ?
                    """,
                    (
                        _shift_iso_timestamp(row["created_at"], days=days),
                        _shift_iso_timestamp(row["updated_at"], days=days),
                        _shift_iso_timestamp(row["last_hit_at"], days=days),
                        row["id"],
                    ),
                )
            if candidate_ids is None:
                candidate_rows = conn.execute(
                    """
                    SELECT candidate_id, created_at, updated_at
                    FROM memory_candidates
                    """
                ).fetchall()
            elif candidate_ids:
                placeholders = ",".join("?" * len(candidate_ids))
                candidate_rows = conn.execute(
                    f"SELECT candidate_id, created_at, updated_at FROM memory_candidates WHERE candidate_id IN ({placeholders})",
                    candidate_ids,
                )
            else:
                candidate_rows = []
            for row in candidate_rows:
                conn.execute(
                    """
                    UPDATE memory_candidates
                    SET created_at = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (
                        _shift_iso_timestamp(row["created_at"], days=days),
                        _shift_iso_timestamp(row["updated_at"], days=days),
                        row["candidate_id"],
                    ),
                )
            conn.commit()

    def refresh_candidate_meta(self, candidate_id: str, meta: dict[str, Any]) -> Any:
        return self.service.candidate_update(candidate_id, {"meta": meta})

    def history(self, scope: str, record_type: str, key: str) -> list[Any]:
        return self.store.history(scope, record_type, key)

    def raw_sql(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.store._connect() as conn:  # noqa: SLF001
            cursor = conn.execute(query, params)
            return cursor.fetchall()
