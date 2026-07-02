"""Evidence collectors for workflow-learning bundles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openminion.modules.skill.models import normalize_text_list, stable_hash

from .shapes import (
    WorkflowEvidenceBundle,
    command_fingerprint,
    normalize_category,
)


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _status_to_outcome(status: object) -> str:
    value = getattr(status, "value", status)
    text = str(value or "").strip().lower()
    if text in {"completed", "success", "succeeded", "passed"}:
        return "success"
    if text in {"failed", "failure", "blocked", "cancelled", "canceled"}:
        return "failure"
    return "partial"


def _artifact_type(ref: str) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    if "://" in text:
        return text.split("://", 1)[0]
    if ":" in text:
        return text.split(":", 1)[0]
    if "." in text.rsplit("/", 1)[-1]:
        return text.rsplit(".", 1)[-1].lower()
    return "artifact"


def _proof_packet_ref(packet: Any) -> str:
    run_id = str(_field(packet, "run_id", "") or "").strip()
    return f"autonomy_proof:{run_id}" if run_id else "autonomy_proof:unknown"


def bundle_from_autonomy_proof_packet(
    packet: Any,
    *,
    intent_category: str,
    capability_category: str,
    strategy_id: str,
    tool_names: list[str] | tuple[str, ...] = (),
    user_correction_refs: list[str] | tuple[str, ...] = (),
    explicit_save: bool = False,
    actor_id: str = "",
    observed_at: str = "",
) -> WorkflowEvidenceBundle:
    """Build a redacted evidence bundle from ``AutonomyProofPacket``-like data."""

    commands = tuple(_field(packet, "commands_run", ()) or ())
    tests = tuple(_field(packet, "tests_run", ()) or ())
    artifacts = normalize_text_list(list(_field(packet, "artifact_refs", ()) or ()))
    source_run_ref = str(_field(packet, "run_id", "") or "").strip()
    validation_summary = str(_field(packet, "validation_summary", "") or "").strip()
    command_fingerprints = [
        command_fingerprint(_field(item, "command", "")) for item in commands
    ]
    test_fingerprints = [
        command_fingerprint(_field(item, "command", "")) for item in tests
    ]
    evidence_refs = [_proof_packet_ref(packet), *artifacts, *user_correction_refs]
    return WorkflowEvidenceBundle(
        source_run_refs=[source_run_ref] if source_run_ref else [],
        proof_packet_refs=[_proof_packet_ref(packet)],
        user_correction_refs=list(user_correction_refs),
        tool_names=list(tool_names),
        command_fingerprints=command_fingerprints,
        test_fingerprints=test_fingerprints,
        artifact_types=[_artifact_type(ref) for ref in artifacts],
        validation_summary=validation_summary,
        outcome=_status_to_outcome(_field(packet, "status")),
        redaction_status="redacted",
        evidence_refs=evidence_refs,
        intent_category=normalize_category("task", intent_category),
        capability_category=normalize_category("capability", capability_category),
        strategy_id=normalize_category("strategy", strategy_id),
        explicit_save=bool(explicit_save),
        actor_id=str(actor_id or "").strip(),
        observed_at=str(observed_at or "").strip(),
    )


def bundle_from_skill_run(
    run: Mapping[str, Any],
    *,
    intent_category: str,
    capability_category: str,
    strategy_id: str,
) -> WorkflowEvidenceBundle:
    """Build workflow evidence from an existing skill-run row."""

    run_id = str(run.get("run_id") or "").strip()
    skill_id = str(run.get("skill_id") or "").strip()
    evidence_refs = normalize_text_list(run.get("evidence_refs") or [])
    ref = f"skill_run:{run_id}" if run_id else f"skill_run:{skill_id}"
    return WorkflowEvidenceBundle(
        source_run_refs=[run_id] if run_id else [],
        skill_run_refs=[ref],
        tool_names=normalize_text_list(run.get("tool_names") or []),
        validation_summary=str(run.get("summary") or ""),
        outcome=_status_to_outcome(run.get("outcome")),
        redaction_status="no_sensitive_fields",
        evidence_refs=[ref, *evidence_refs],
        intent_category=normalize_category("skill", intent_category),
        capability_category=normalize_category("capability", capability_category),
        strategy_id=normalize_category("strategy", strategy_id),
        observed_at=str(run.get("created_at") or ""),
    )


def bundle_from_strategy_outcome(
    card: Mapping[str, Any],
    *,
    explicit_save: bool = False,
    actor_id: str = "",
) -> WorkflowEvidenceBundle:
    """Build evidence from a strategy-outcome card or overlay payload."""

    strategy_id = str(card.get("strategy_id") or card.get("strategy") or "").strip()
    capability_category = str(card.get("capability_category") or "unknown").strip()
    intent_category = str(card.get("intent_category") or "unknown").strip()
    outcome = _status_to_outcome(card.get("outcome") or card.get("status"))
    card_ref = str(card.get("outcome_ref") or card.get("record_ref") or "").strip()
    if not card_ref:
        card_ref = f"strategy_outcome:{stable_hash(card)[:16]}"
    return WorkflowEvidenceBundle(
        strategy_outcome_refs=[card_ref],
        tool_names=normalize_text_list(card.get("tool_names") or []),
        command_fingerprints=normalize_text_list(
            card.get("command_fingerprints") or []
        ),
        test_fingerprints=normalize_text_list(card.get("test_fingerprints") or []),
        artifact_types=normalize_text_list(card.get("artifact_types") or []),
        validation_summary=str(card.get("summary") or ""),
        outcome=outcome,
        redaction_status="no_sensitive_fields",
        evidence_refs=[card_ref],
        intent_category=normalize_category("task", intent_category),
        capability_category=normalize_category("capability", capability_category),
        strategy_id=normalize_category("strategy", strategy_id),
        explicit_save=bool(explicit_save),
        actor_id=str(actor_id or "").strip(),
        observed_at=str(card.get("observed_at") or ""),
    )


__all__ = (
    "bundle_from_autonomy_proof_packet",
    "bundle_from_skill_run",
    "bundle_from_strategy_outcome",
)
