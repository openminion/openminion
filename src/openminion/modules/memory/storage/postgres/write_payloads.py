import datetime
from typing import Any, Literal

from ...models import ArtifactRef
from ..base import register_feedback_command
from .sql import _clamp01, _json_loads


def _upsert_payload(
    store: Any, row: dict[str, Any] | None, record_patch: dict[str, Any]
) -> dict[str, Any]:
    if row is None:
        return {
            "title": record_patch.get("title"),
            "content": record_patch.get("content", {}),
            "tags": list(record_patch.get("tags", [])),
            "entities": list(record_patch.get("entities", [])),
            "source": str(record_patch.get("source", "agent_inferred")),
            "confidence": float(record_patch.get("confidence", 0.5)),
            "evidence_refs": list(record_patch.get("evidence_refs", [])),
            "meta": dict(record_patch.get("meta", {})),
            "expires_at": record_patch.get("expires_at"),
        }
    row_content = _json_loads(row.get("content_json"), {})
    patch_content = record_patch.get("content", row_content)
    if isinstance(row_content, dict) and isinstance(record_patch.get("content"), dict):
        content = dict(row_content)
        content.update(dict(record_patch.get("content", {})))
    else:
        content = patch_content
    return {
        "title": record_patch.get("title", row.get("title")),
        "content": content,
        "tags": list(record_patch.get("tags", _json_loads(row.get("tags_json"), []))),
        "entities": list(
            record_patch.get("entities", _json_loads(row.get("entities_json"), []))
        ),
        "source": str(record_patch.get("source", row["source"])),
        "confidence": float(record_patch.get("confidence", row.get("confidence", 0.5))),
        "evidence_refs": list(
            record_patch.get(
                "evidence_refs",
                [
                    ArtifactRef(**item)
                    for item in store._decode_evidence_ref_values(
                        row.get("evidence_json")
                    )
                ],
            )
        ),
        "meta": dict(record_patch.get("meta", _json_loads(row.get("meta_json"), {}))),
        "expires_at": record_patch.get("expires_at", row.get("expires_at")),
    }


def _feedback_update_values(
    row: dict[str, Any],
    *,
    outcome: Literal["success", "failed", "timeout"],
    command_id: str,
    observed_at: str,
    feedback_delta: float,
) -> dict[str, Any] | None:
    updated_at = str(observed_at or "").strip()
    if not updated_at:
        updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    meta = dict(_json_loads(row.get("meta_json"), {}))
    normalized_command_id = str(command_id or "").strip()
    if not register_feedback_command(meta, normalized_command_id):
        return None
    existing_feedback = _clamp01(float(meta.get("feedback_score", 0.0) or 0.0))
    meta["feedback_score"] = _clamp01(existing_feedback + float(feedback_delta))
    meta.setdefault("outcome_success_count", 0)
    meta.setdefault("outcome_failure_count", 0)
    counter_key = (
        "outcome_success_count" if outcome == "success" else "outcome_failure_count"
    )
    meta[counter_key] = int(meta[counter_key] or 0) + 1
    meta["last_outcome_at"] = updated_at
    meta["last_outcome_status"] = outcome
    meta["last_outcome_command_id"] = normalized_command_id
    return {"meta": meta, "updated_at": updated_at}


__all__ = ["_feedback_update_values", "_upsert_payload"]
