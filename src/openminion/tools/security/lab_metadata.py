"""Trusted metadata projection for the opt-in local security lab."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError

SECURITY_LAB_ALLOWED_TOOL_IDS = frozenset(
    {"respond", "clarify", "exec.run", "security.publish_report"}
)
_SECURITY_LAB_METADATA_KEYS = frozenset(
    {
        "lab_required",
        "activity_class",
        "security_lab_state",
        "security_lab_reason",
        "activation_id",
        "activation_expires_at",
        "profile_revision",
        "profile_version",
        "config_fingerprint",
        "resolved_scope_fingerprint",
        "daemon_id",
        "target_container_id",
        "target_image_id",
        "worker_image_digest",
        "isolation_mode",
        "security_lab_target",
    }
)


def security_lab_scope_fingerprint(
    *,
    runner_scope_fingerprint: str,
    session_id: str,
    agent_id: str,
    profile_revision: int,
    profile_version: str,
    allowed_tools: Iterable[str],
) -> str:
    payload = {
        "runner_scope_fingerprint": runner_scope_fingerprint,
        "session_id": session_id,
        "agent_id": agent_id,
        "profile_revision": int(profile_revision),
        "profile_version": profile_version,
        "allowed_tools": sorted(str(item) for item in allowed_tools),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def resolve_security_lab_metadata(
    exposure_service: Any,
    *,
    config: Any,
    runner: Any,
    identity: Mapping[str, Any],
    session_id: str,
) -> dict[str, Any]:
    if not bool(identity.get("lab_required")):
        return {}
    metadata: dict[str, Any] = {
        "lab_required": "true",
        "activity_class": "local_lab_active",
        "security_lab_state": "unavailable",
    }
    allowed_tools = frozenset(identity.get("allowed_tools", ()))
    if (
        identity.get("tool_use") != "restricted"
        or allowed_tools != SECURITY_LAB_ALLOWED_TOOL_IDS
    ):
        metadata["security_lab_reason"] = "security_lab_identity_posture_changed"
        return metadata
    if config is None or runner is None or exposure_service is None:
        metadata["security_lab_reason"] = "security_lab_not_configured"
        return metadata
    try:
        activation = exposure_service.resolve_exact_activation(
            "security_lab", session_id=session_id
        )
        scope = runner.preflight()
    except (ToolRuntimeError, RuntimeError) as exc:
        metadata["security_lab_reason"] = str(exc)
        return metadata

    expected_fingerprint = security_lab_scope_fingerprint(
        runner_scope_fingerprint=scope.resolved_scope_fingerprint,
        session_id=session_id,
        agent_id=str(identity.get("agent_id", "")),
        profile_revision=int(identity.get("profile_revision", 0) or 0),
        profile_version=str(identity.get("profile_version", "")),
        allowed_tools=allowed_tools,
    )
    matches = (
        activation.agent_id == str(identity.get("agent_id", ""))
        and activation.profile_revision
        == int(identity.get("profile_revision", 0) or 0)
        and activation.profile_version == str(identity.get("profile_version", ""))
        and activation.config_fingerprint == scope.config_fingerprint
        and activation.daemon_id == scope.daemon_id
        and activation.target_container_id == scope.target_container_id
        and activation.target_image_id == scope.target_image_id
        and activation.worker_image_digest == scope.worker_image_digest
        and activation.resolved_scope_fingerprint == expected_fingerprint
        and activation.target_id == config.target_container
    )
    if not matches:
        metadata["security_lab_reason"] = "security_lab_scope_drift"
        return metadata

    metadata.update(
        security_lab_state="ready",
        security_lab_reason="",
        activation_id=activation.audit_id,
        activation_expires_at=str(activation.expires_at or ""),
        agent_id=activation.agent_id,
        profile_revision=str(activation.profile_revision),
        profile_version=activation.profile_version,
        config_fingerprint=activation.config_fingerprint,
        resolved_scope_fingerprint=activation.resolved_scope_fingerprint,
        daemon_id=activation.daemon_id,
        target_container_id=activation.target_container_id,
        target_image_id=activation.target_image_id,
        worker_image_digest=activation.worker_image_digest,
        isolation_mode=activation.isolation_mode,
        security_lab_target=activation.target_id,
    )
    return metadata


def project_security_lab_metadata(
    metadata: dict[str, Any], trusted: Mapping[str, Any]
) -> None:
    lab_fields_present = (
        any(
            key in metadata
            for key in ("lab_required", "security_lab_state", "security_lab_target")
        )
        or metadata.get("activity_class") == "local_lab_active"
    )
    if not trusted and not lab_fields_present:
        return
    for key in _SECURITY_LAB_METADATA_KEYS:
        metadata.pop(key, None)
    metadata.update(trusted)


__all__ = [
    "SECURITY_LAB_ALLOWED_TOOL_IDS",
    "project_security_lab_metadata",
    "resolve_security_lab_metadata",
    "security_lab_scope_fingerprint",
]
