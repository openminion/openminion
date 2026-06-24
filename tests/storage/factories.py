from __future__ import annotations

import json
from typing import Any

from faker import Faker


_FAKER = Faker()
Faker.seed(42)


def reset_seed(seed: int = 42) -> None:
    Faker.seed(seed)


def _iso() -> str:
    return _FAKER.date_time_this_year().isoformat(timespec="seconds")


def _apply_overrides(row: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    if overrides:
        row.update(overrides)
    return row


def make_secret_payload(**overrides: Any) -> dict[str, Any]:
    now = float(_FAKER.unix_time())
    row = {
        "key": _FAKER.slug(),
        "namespace": "default",
        "value": _FAKER.sha256(),
        "created_at": now,
        "updated_at": now,
    }
    return _apply_overrides(row, overrides)


def make_session_record(**overrides: Any) -> dict[str, Any]:
    now = _iso()
    row = {
        "session_id": _FAKER.uuid4(),
        "created_at": now,
        "updated_at": now,
        "title": _FAKER.sentence(nb_words=4),
        "status": "active",
        "active_agent_id": _FAKER.uuid4(),
        "participants_json": "[]",
        "root_goal": _FAKER.sentence(),
        "tags_json": "[]",
        "config_snapshot_ref": None,
        "meta_json": "{}",
    }
    return _apply_overrides(row, overrides)


def make_memory_event(**overrides: Any) -> dict[str, Any]:
    now = _iso()
    row = {
        "id": _FAKER.uuid4(),
        "scope": "agent:test",
        "type": "fact",
        "key": _FAKER.slug(),
        "title": _FAKER.sentence(nb_words=3),
        "content_json": json.dumps({"text": _FAKER.sentence()}),
        "tags_json": "[]",
        "entities_json": "[]",
        "source": "test",
        "confidence": 0.75,
        "evidence_json": "[]",
        "meta_json": "{}",
        "last_hit_at": None,
        "tier": "working",
        "access_count": 0,
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
        "supersedes_id": None,
        "superseded_by_id": None,
    }
    return _apply_overrides(row, overrides)


def make_telemetry_event(**overrides: Any) -> dict[str, Any]:
    row = {
        "session_id": _FAKER.uuid4(),
        "turn_id": _FAKER.uuid4(),
        "event_type": _FAKER.random_element(("turn.start", "turn.end", "tool.call")),
        "timestamp": float(_FAKER.unix_time()),
        "data": json.dumps({"payload": _FAKER.sentence()}),
    }
    return _apply_overrides(row, overrides)


def make_a2a_agent(**overrides: Any) -> dict[str, Any]:
    row = {
        "agent_id": _FAKER.uuid4(),
        "capabilities_json": "[]",
        "endpoint": _FAKER.url(),
        "tags_json": "[]",
        "status": "active",
        "updated_at": _iso(),
    }
    return _apply_overrides(row, overrides)


def make_artifact_record(**overrides: Any) -> dict[str, Any]:
    row = {
        "sha256": _FAKER.sha256(),
        "size_bytes": _FAKER.random_int(min=1, max=1_000_000),
        "mime": "application/octet-stream",
        "created_at": _iso(),
        "original_name": _FAKER.file_name(),
        "original_path": None,
        "label": None,
        "session_id": _FAKER.uuid4(),
        "trace_id": _FAKER.uuid4(),
        "agent_id": _FAKER.uuid4(),
        "encoding": None,
        "deleted_at": None,
        "meta_json": "{}",
    }
    return _apply_overrides(row, overrides)


def make_controlplane_principal(**overrides: Any) -> dict[str, Any]:
    now = _iso()
    row = {
        "principal_id": _FAKER.uuid4(),
        "created_at": now,
        "updated_at": now,
        "meta_json": "{}",
    }
    return _apply_overrides(row, overrides)


def make_identity_profile(**overrides: Any) -> dict[str, Any]:
    row = {
        "agent_id": _FAKER.uuid4(),
        "profile_json": json.dumps({"name": _FAKER.name()}),
        "profile_revision": 1,
        "profile_version": "1.0",
        "updated_at": _iso(),
    }
    return _apply_overrides(row, overrides)


def make_policy_grant(**overrides: Any) -> dict[str, Any]:
    now = _iso()
    row = {
        "grant_id": _FAKER.uuid4(),
        "subject_id": _FAKER.uuid4(),
        "effect": "allow",
        "tool": _FAKER.slug(),
        "method": "*",
        "target_json": "{}",
        "risk_floor": None,
        "duration_type": "permanent",
        "expires_at": None,
        "session_id": None,
        "invocation_hash": None,
        "max_uses": None,
        "uses_count": 0,
        "created_at": now,
        "updated_at": now,
        "revoked_at": None,
        "reason": None,
        "created_trace_id": _FAKER.uuid4(),
    }
    return _apply_overrides(row, overrides)


def make_registry_agent(**overrides: Any) -> dict[str, Any]:
    row = {
        "agent_id": _FAKER.uuid4(),
        "descriptor_json": "{}",
        "source": "test",
        "updated_at": _iso(),
    }
    return _apply_overrides(row, overrides)


def make_retrieve_doc(**overrides: Any) -> dict[str, Any]:
    row = {
        "doc_id": _FAKER.uuid4(),
        "source_type": "file",
        "source_ref": _FAKER.file_path(),
    }
    return _apply_overrides(row, overrides)


def make_skill_record(**overrides: Any) -> dict[str, Any]:
    now = _iso()
    row = {
        "skill_id": _FAKER.uuid4(),
        "name": _FAKER.slug(),
        "status": "active",
        "scope": "agent",
        "agent_id": _FAKER.uuid4(),
        "created_at": now,
        "updated_at": now,
    }
    return _apply_overrides(row, overrides)


def make_task_record(**overrides: Any) -> dict[str, Any]:
    now = _iso()
    row = {
        "task_id": _FAKER.uuid4(),
        "title": _FAKER.sentence(nb_words=4),
        "description": _FAKER.paragraph(),
        "status": "PENDING",
        "due_at": None,
        "scheduled_at": None,
        "wait_at": None,
        "created_by_mode": "test",
        "executing_mode": None,
        "current_plan_id": None,
        "next_step_id": None,
        "created_at": now,
    }
    return _apply_overrides(row, overrides)


def make_storage_meta_entry(**overrides: Any) -> dict[str, Any]:
    row = {
        "key": _FAKER.slug(),
        "value": _FAKER.sentence(),
    }
    return _apply_overrides(row, overrides)


FACTORIES: dict[str, Any] = {
    "secret": make_secret_payload,
    "session": make_session_record,
    "memory": make_memory_event,
    "telemetry": make_telemetry_event,
    "a2a": make_a2a_agent,
    "artifact": make_artifact_record,
    "controlplane": make_controlplane_principal,
    "identity": make_identity_profile,
    "policy": make_policy_grant,
    "registry": make_registry_agent,
    "retrieve": make_retrieve_doc,
    "skill": make_skill_record,
    "task": make_task_record,
    "storage": make_storage_meta_entry,
}

assert len(FACTORIES) == 14, (
    f"SMP-25 exit criterion requires 14 per-module factories; got {len(FACTORIES)}"
)


__all__ = [
    "FACTORIES",
    "reset_seed",
    "make_secret_payload",
    "make_session_record",
    "make_memory_event",
    "make_telemetry_event",
    "make_a2a_agent",
    "make_artifact_record",
    "make_controlplane_principal",
    "make_identity_profile",
    "make_policy_grant",
    "make_registry_agent",
    "make_retrieve_doc",
    "make_skill_record",
    "make_task_record",
    "make_storage_meta_entry",
]
