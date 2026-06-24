from __future__ import annotations

import hashlib
import json
from typing import Any, Callable
from uuid import uuid4

from openminion.cli.tui.project_context import (
    build_project_context_metadata,
    resolve_project_context,
)


def resolve_conversation_id(
    args: Any,
    *,
    session_id: str,
    resolve_conversation_selection_fn: Callable[..., dict[str, str]],
) -> str:
    return resolve_conversation_selection_fn(
        args,
        session_id=session_id,
    )["conversation_id"]


def generate_conversation_id() -> str:
    return f"conv-{uuid4().hex}"


def resolve_conversation_selection(
    args: Any,
    *,
    session_id: str,
    config_path: str | None = None,
    force_fresh: bool = False,
    conversation_env_name: str,
    resolve_environment_config_fn: Callable[[], dict[str, Any]],
    latest_session_conversation_id_fn: Callable[..., str],
    generate_conversation_id_fn: Callable[[], str],
) -> dict[str, str]:
    explicit = str(getattr(args, "conversation", "") or "").strip()
    if explicit:
        return {"conversation_id": explicit, "source": "explicit"}
    env_value = (
        resolve_environment_config_fn()
        .get(
            conversation_env_name,
            "",
        )
        .strip()
    )
    if env_value:
        return {"conversation_id": env_value, "source": "env"}
    if force_fresh:
        return {
            "conversation_id": generate_conversation_id_fn(),
            "source": "force_fresh",
        }
    if bool(getattr(args, "reset_session", False)):
        return {"conversation_id": generate_conversation_id_fn(), "source": "reset"}
    latest_conversation_id = latest_session_conversation_id_fn(
        session_id=session_id,
        config_path=config_path,
    )
    if latest_conversation_id:
        return {
            "conversation_id": latest_conversation_id,
            "source": "session_reuse",
        }
    return {"conversation_id": generate_conversation_id_fn(), "source": "fresh"}


def build_turn_idempotency_key(
    *,
    agent_id: str,
    session_id: str,
    conversation_id: str,
    thread_id: str,
    turn_nonce: str,
    prefix: str,
) -> str:
    material = "|".join(
        (
            agent_id,
            session_id,
            conversation_id,
            thread_id,
            turn_nonce,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def build_lifecycle_payload(
    *,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    source: str = "cli-chat",
) -> dict[str, str]:
    return {
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "attach_id": attach_id,
        "source": source,
    }


def resolve_lifecycle_state(
    args: Any,
    *,
    session_id: str,
    config_path: str | None,
    force_fresh: bool = False,
    resolve_conversation_selection_fn: Callable[..., dict[str, str]],
    build_lifecycle_payload_fn: Callable[..., dict[str, str]],
) -> tuple[dict[str, str], str, str, str, dict[str, str]]:
    conversation_selection = resolve_conversation_selection_fn(
        args,
        session_id=session_id,
        config_path=config_path,
        force_fresh=force_fresh,
    )
    conversation_id = conversation_selection["conversation_id"]
    thread_id = (
        "" if conversation_selection["source"] == "session_reuse" else conversation_id
    )
    attach_id = f"att-{uuid4().hex}"
    lifecycle_payload = build_lifecycle_payload_fn(
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
    )
    return (
        conversation_selection,
        conversation_id,
        thread_id,
        attach_id,
        lifecycle_payload,
    )


def build_run_profile_override_payload(
    args: Any,
    *,
    run_profile_overrides_from_mapping_fn: Callable[[dict[str, Any]], Any],
) -> dict[str, str]:
    overrides = run_profile_overrides_from_mapping_fn(vars(args))
    payload: dict[str, str] = {}
    if overrides.provider:
        payload["override_provider"] = overrides.provider
    if overrides.model:
        payload["override_model"] = overrides.model
    if overrides.system_prompt:
        payload["override_system_prompt"] = overrides.system_prompt
    return payload


def build_inbound_metadata(
    *,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    resume_requested: bool,
    reset_requested: bool,
    cwd: str | None = None,
    recent_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    payload = {
        "source": "openminion.chat",
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "attach_id": attach_id,
        "resume": str(resume_requested).lower(),
        "reset_session": str(reset_requested).lower(),
    }
    normalized_cwd = str(cwd or "").strip()
    if normalized_cwd:
        payload["cwd"] = normalized_cwd
        project_context = resolve_project_context(normalized_cwd)
        if project_context is not None:
            for key, value in build_project_context_metadata(project_context).items():
                payload.setdefault(key, value)
    normalized_recent_artifacts = _normalize_recent_artifacts(recent_artifacts)
    if normalized_recent_artifacts:
        payload["recent_artifacts"] = json.dumps(
            normalized_recent_artifacts,
            sort_keys=True,
        )
    return payload


def _normalize_recent_artifacts(
    recent_artifacts: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    if not isinstance(recent_artifacts, list):
        return []

    normalized: list[dict[str, str]] = []
    for artifact in recent_artifacts[-3:]:
        if not isinstance(artifact, dict):
            continue
        item: dict[str, str] = {}
        ref_text = str(artifact.get("ref", "") or "").strip()
        path_text = str(artifact.get("path", "") or "").strip()
        kind_text = str(
            artifact.get("role", "")
            or artifact.get("kind", "")
            or artifact.get("type", "")
            or ""
        ).strip()
        if ref_text:
            item["ref"] = ref_text
        if path_text:
            item["path"] = path_text
        if kind_text:
            item["kind"] = kind_text
        if item:
            normalized.append(item)
    return normalized
