from __future__ import annotations

from datetime import datetime
from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)


class RuntimeMemoryProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(
        self,
        memory_service: Any | None,
        *,
        agent_id: str,
        session_id: str,
        record_limit: int = 50,
        candidate_limit: int = 50,
        search_limit: int = 50,
    ) -> None:
        self._memory_service = memory_service
        self._agent_id = str(agent_id or "").strip()
        self._session_id = str(session_id or "").strip()
        self._record_limit = max(1, int(record_limit))
        self._candidate_limit = max(1, int(candidate_limit))
        self._search_limit = max(1, int(search_limit))

    def list_records(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._memory_service is None:
            return []
        list_records = getattr(self._memory_service, "list", None)
        if not callable(list_records):
            return []

        safe_limit = max(1, int(limit))
        try:
            rows = list_records(
                ListQueryOptions(
                    scopes=self._scopes,
                    limit=min(safe_limit, self._record_limit),
                )
            )
        except Exception:
            return []

        if not isinstance(rows, list):
            return []
        return [self._map_record(row) for row in rows]

    def list_candidates(self) -> list[dict[str, Any]]:
        if self._memory_service is None:
            return []
        candidate_list = getattr(self._memory_service, "candidate_list", None)
        if not callable(candidate_list):
            return []
        if not self._session_id:
            return []

        try:
            rows = candidate_list(
                CandidateListOptions(
                    session_id=self._session_id,
                    status="proposed",
                    limit=self._candidate_limit,
                )
            )
        except Exception:
            return []
        if not isinstance(rows, list):
            return []

        output: list[dict[str, Any]] = []
        for row in rows:
            candidate_id = str(
                self._value(row, "candidate_id") or self._value(row, "id") or ""
            ).strip()
            if not candidate_id:
                continue
            output.append(
                {
                    "id": candidate_id,
                    "content_preview": self._preview(
                        self._value(row, "title") or self._value(row, "content")
                    ),
                    "score": self._score(row),
                }
            )
        return output

    def search(self, query: str) -> list[dict[str, Any]]:
        if self._memory_service is None:
            return []
        search_records = getattr(self._memory_service, "search", None)
        if not callable(search_records):
            return []

        normalized_query = str(query or "").strip()
        if not normalized_query:
            return self.list_records(limit=self._search_limit)

        try:
            rows = search_records(
                SearchQueryOptions(
                    query=normalized_query,
                    scopes=self._scopes,
                    limit=self._search_limit,
                )
            )
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        return [self._map_record(row) for row in rows]

    @property
    def _scopes(self) -> list[str]:
        scopes: list[str] = []
        if self._session_id:
            scopes.append(f"session:{self._session_id}")
        if self._agent_id:
            scopes.append(f"agent:{self._agent_id}")
        scopes.append("global:default")
        return scopes

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _preview(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw[:80]
        if isinstance(raw, dict):
            summary = str(raw.get("summary") or raw.get("text") or raw)
            return summary[:80]
        return str(raw)[:80]

    @staticmethod
    def _score(row: Any) -> float:
        confidence = RuntimeMemoryProvider._value(row, "confidence", 0.0)
        try:
            return max(0.0, min(1.0, float(confidence or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _map_record(row: Any) -> dict[str, Any]:
        record_id = str(RuntimeMemoryProvider._value(row, "id") or "").strip()
        record_type = str(RuntimeMemoryProvider._value(row, "type") or "")
        scope = str(RuntimeMemoryProvider._value(row, "scope") or "")
        content = RuntimeMemoryProvider._value(
            row, "title"
        ) or RuntimeMemoryProvider._value(row, "content")
        updated_at = RuntimeMemoryProvider._value(
            row, "updated_at"
        ) or RuntimeMemoryProvider._value(row, "created_at")
        ts = RuntimeMemoryProvider._to_day(updated_at)
        return {
            "id": record_id,
            "type": record_type,
            "scope": scope,
            "content_preview": RuntimeMemoryProvider._preview(content),
            "content": str(content or ""),
            "metadata": RuntimeMemoryProvider._value(row, "metadata", {}),
            "ts": ts,
        }

    @staticmethod
    def _to_day(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, datetime):
            return raw.date().isoformat()
        text = str(raw).strip()
        if not text:
            return ""
        try:
            return (
                datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
            )
        except ValueError:
            return text[:10]
