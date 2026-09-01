import json
from pathlib import Path
from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas.base import iso_now, new_uuid


class LocalMemoryAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "memory.jsonl"

    def put_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str:
        record_id = f"mem_{new_uuid()}"
        payload = {
            "id": record_id,
            "kind": "record",
            "scope": scope,
            "record_type": record_type,
            "title": title,
            "content": content,
            "tags": tags or [],
            "evidence_refs": evidence_refs or [],
            "ts": iso_now(),
        }
        self._append(payload)
        return record_id

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        candidate_id = f"cand_{new_uuid()}"
        payload = {
            "id": candidate_id,
            "kind": "candidate",
            "scope": scope,
            "record_type": record_type,
            "title": title,
            "content": content,
            "tags": tags or [],
            "evidence_refs": evidence_refs or [],
            "confidence": float(confidence) if confidence is not None else None,
            "meta": dict(meta or {}),
            "ts": iso_now(),
        }
        self._append(payload)
        return candidate_id

    def apply_outcome_feedback(
        self,
        *,
        record_ids: list[str],
        outcome: str,
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        normalized_ids = list(
            dict.fromkeys(record_id.strip() for record_id in record_ids)
        )
        normalized_ids = [record_id for record_id in normalized_ids if record_id]
        if not normalized_ids:
            return 0
        self._append(
            {
                "kind": "outcome_feedback",
                "record_ids": normalized_ids,
                "outcome": str(outcome or "").strip(),
                "command_id": str(command_id or "").strip(),
                "observed_at": str(observed_at or "").strip(),
                "feedback_delta": float(feedback_delta),
                "ts": iso_now(),
            }
        )
        return len(normalized_ids)

    def apply_capture_bundle(self, **_: Any) -> dict[str, Any]:
        from openminion.modules.memory.runtime.capture_bundle import (
            CaptureRecoveryUnsupportedError,
        )

        raise CaptureRecoveryUnsupportedError(
            "local JSONL memory does not support atomic capture bundles"
        )

    def _append(self, payload: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
