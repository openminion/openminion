from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from openminion.base.time import utc_now_iso
from openminion.modules.skill.models import stable_hash
from openminion.modules.skill.storage.base import SkillStore
from openminion.modules.skill.storage.workflow_observations import (
    WorkflowObservationStorageError,
)

from .miner import WorkflowShapeMiner
from .shapes import WorkflowEvidenceBundle, WorkflowShape

_RUNTIME_STRATEGY_ID = "runtime:successful_tools_v1"
_INTENT_CATEGORIES = frozenset(
    {"analyze", "create", "modify", "verify", "operate", "research", "communicate"}
)
_CAPABILITY_CATEGORIES = frozenset(
    {"code", "files", "shell", "web", "browser", "data", "system", "collaboration"}
)
WorkflowObservationStatus = Literal[
    "observed", "duplicate", "conflict", "authoring_ready"
]


class WorkflowObservationError(RuntimeError):
    pass


class WorkflowObservationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorkflowObservationStatus
    observation_id: str
    observation_count: int
    shape_id: str = ""
    matching_success_count: int = 0


def _stored_bundles(
    store: SkillStore, *, agent_id: str
) -> list[WorkflowEvidenceBundle]:
    try:
        rows = store.list_workflow_observations(agent_id=agent_id)
    except WorkflowObservationStorageError as exc:
        raise WorkflowObservationError("workflow observation storage failed") from exc
    try:
        return [
            WorkflowEvidenceBundle.model_validate_json(str(row["bundle_json"]))
            for row in rows
        ]
    except (json.JSONDecodeError, KeyError, ValidationError) as exc:
        raise WorkflowObservationError(
            "stored workflow observation is invalid"
        ) from exc


def list_runtime_workflow_observations(
    store: SkillStore, *, agent_id: str
) -> list[dict[str, object]]:
    agent = str(agent_id or "").strip()
    if not agent:
        raise WorkflowObservationError("agent_id is required")
    return [
        bundle.model_dump(mode="json")
        for bundle in _stored_bundles(store, agent_id=agent)
    ]


def list_runtime_workflow_shapes(
    store: SkillStore, *, agent_id: str
) -> list[WorkflowShape]:
    agent = str(agent_id or "").strip()
    if not agent:
        raise WorkflowObservationError("agent_id is required")
    return WorkflowShapeMiner().mine(_stored_bundles(store, agent_id=agent))


def observe_runtime_workflow(
    store: SkillStore,
    *,
    agent_id: str,
    source_run_ref: str,
    intent_category: str,
    capability_category: str,
    tool_names: list[str],
) -> WorkflowObservationResult:
    agent = str(agent_id or "").strip()
    source_ref = str(source_run_ref or "").strip()
    tools = sorted(
        {str(name or "").strip() for name in tool_names if str(name or "").strip()}
    )
    if not agent or not source_ref:
        raise WorkflowObservationError("agent_id and source_run_ref are required")
    if not tools:
        raise WorkflowObservationError(
            "at least one successful substantive tool is required"
        )
    if intent_category not in _INTENT_CATEGORIES:
        raise WorkflowObservationError("unsupported workflow intent category")
    if capability_category not in _CAPABILITY_CATEGORIES:
        raise WorkflowObservationError("unsupported workflow capability category")

    observed_at = utc_now_iso()
    trace_ref = f"trace:{source_ref}"
    bundle = WorkflowEvidenceBundle(
        source_run_refs=[source_ref],
        tool_names=tools,
        outcome="success",
        redaction_status="no_sensitive_fields",
        evidence_refs=[trace_ref],
        intent_category=f"intent:{intent_category}",
        capability_category=f"capability:{capability_category}",
        strategy_id=_RUNTIME_STRATEGY_ID,
        actor_id=agent,
        observed_at=observed_at,
        risk_level="medium",
    )
    bundle_id = f"wlev-{stable_hash({'agent_id': agent, 'source_run_ref': source_ref, 'provenance_checksum': bundle.provenance_checksum})[:16]}"
    bundle = bundle.model_copy(update={"bundle_id": bundle_id})

    try:
        created = store.insert_workflow_observation(
            agent_id=agent,
            source_run_ref=source_ref,
            bundle_id=bundle.bundle_id,
            provenance_checksum=bundle.provenance_checksum,
            bundle_json=bundle.model_dump_json(),
            created_at=observed_at,
        )
        if not created:
            existing = store.get_workflow_observation(
                agent_id=agent,
                source_run_ref=source_ref,
            )
            if existing is None:
                raise WorkflowObservationError(
                    "workflow observation insert lost its identity row"
                )
            if (
                str(existing.get("provenance_checksum") or "")
                != bundle.provenance_checksum
            ):
                return WorkflowObservationResult(
                    status="conflict",
                    observation_id=str(existing.get("bundle_id") or ""),
                    observation_count=len(
                        store.list_workflow_observations(agent_id=agent)
                    ),
                )
            stored_id = str(existing.get("bundle_id") or "")
        else:
            stored_id = bundle.bundle_id
        bundles = _stored_bundles(store, agent_id=agent)
    except WorkflowObservationError:
        raise
    except WorkflowObservationStorageError as exc:
        raise WorkflowObservationError("workflow observation storage failed") from exc

    shapes = WorkflowShapeMiner().mine(bundles)
    shape = next(
        (item for item in shapes if trace_ref in item.evidence_refs),
        None,
    )
    status: WorkflowObservationStatus = "duplicate" if not created else "observed"
    if created and shape is not None and WorkflowShapeMiner().is_skill_ready(shape):
        status = "authoring_ready"
    return WorkflowObservationResult(
        status=status,
        observation_id=stored_id,
        observation_count=len(bundles),
        shape_id=shape.shape_id if shape is not None else "",
        matching_success_count=shape.success_count if shape is not None else 0,
    )


__all__ = (
    "WorkflowObservationError",
    "WorkflowObservationResult",
    "list_runtime_workflow_observations",
    "list_runtime_workflow_shapes",
    "observe_runtime_workflow",
)
