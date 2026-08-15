from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

from openminion.modules.memory.errors import MemctlError


MemoryEventEmitter = Callable[..., None]

MEMORY_CONTEXT_BUILD_FAILED_CODE = "MEMORY_CONTEXT_BUILD_FAILED"
MEMORY_CONTEXT_BUILD_FAILED_REASON = "memory_context_build_failed"
MEMORY_WRITE_FAILED_CODE = "MEMORY_WRITE_FAILED"
MEMORY_WRITE_FAILED_REASON = "memory_write_failed"
MEMORY_CAPSULE_REFRESH_FAILED_CODE = "MEMORY_CAPSULE_REFRESH_FAILED"
MEMORY_CAPSULE_REFRESH_FAILED_REASON = "memory_capsule_refresh_failed"
MEMORY_FOLLOWUP_FAILED_CODE = "MEMORY_FOLLOWUP_FAILED"
MEMORY_FOLLOWUP_FAILED_REASON = "memory_followup_failed"


def memory_error_facts(
    exc: Exception,
    *,
    fallback_code: str,
    fallback_reason: str,
) -> dict[str, str]:
    if isinstance(exc, MemctlError):
        code = str(getattr(exc, "code", "") or "").strip() or fallback_code
        details = dict(getattr(exc, "details", {}) or {})
        reason_code = str(details.get("reason_code", "") or "").strip()
        return {
            "error_code": code,
            "reason_code": reason_code or fallback_reason,
            "error_type": type(exc).__name__,
        }
    return {
        "error_code": fallback_code,
        "reason_code": fallback_reason,
        "error_type": type(exc).__name__,
    }


def text_fingerprint(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def derive_memory_patch_id(
    *,
    agent_memory: Any,
    session_id: str,
    run_id: str,
    request_id: str,
    user_message: str,
) -> str:
    derive_patch_id = getattr(agent_memory, "derive_patch_id", None)
    if not callable(derive_patch_id):
        return ""
    return str(
        derive_patch_id(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            user_message=user_message,
        )
        or ""
    )


def emit_memory_write_events(
    *,
    emit_memory_event: MemoryEventEmitter,
    session_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    run_id: str,
    request_id: str,
    memory_capsule_strategy: str,
    patch_id_hint: str,
    memory_patch: Any,
    patch_changed: bool,
) -> None:
    emit_memory_event(
        session_id=session_id,
        event_type="memory.write.completed",
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload={
            "run_id": run_id,
            "request_id": request_id,
            "strategy": memory_capsule_strategy,
            "patch_id": str(memory_patch.patch_id or ""),
            "generation": str(int(memory_patch.generation or 0)),
            "facts_added": str(memory_patch.facts_added),
            "todos_added": str(memory_patch.todos_added),
            "todos_completed": str(memory_patch.todos_completed),
            "replayed_patches": str(int(memory_patch.replayed_patches or 0)),
            "lock_recovered": str(bool(memory_patch.lock_recovered)).lower(),
        },
    )
    emit_memory_event(
        session_id=session_id,
        event_type="memory.turn.recorded",
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload={
            "run_id": run_id,
            "request_id": request_id,
            "strategy": memory_capsule_strategy,
            "facts_added": str(memory_patch.facts_added),
            "todos_added": str(memory_patch.todos_added),
            "todos_completed": str(memory_patch.todos_completed),
            "patch_id": str(memory_patch.patch_id or patch_id_hint or ""),
            "changed": str(patch_changed).lower(),
        },
    )


def record_memory_failure(
    *,
    exc: Exception,
    logger: logging.Logger,
    agent_id: str,
    session_id: str,
    run_id: str,
    request_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    memory_capsule_strategy: str,
    patch_id_hint: str,
    emit_memory_event: MemoryEventEmitter,
    outbound_metadata: dict[str, str],
) -> None:
    error_facts = memory_error_facts(
        exc,
        fallback_code=MEMORY_WRITE_FAILED_CODE,
        fallback_reason=MEMORY_WRITE_FAILED_REASON,
    )
    outbound_metadata["memory_enabled"] = "false"
    outbound_metadata["memory_write_error_code"] = error_facts["error_code"]
    outbound_metadata["memory_write_reason_code"] = error_facts["reason_code"]
    logger.warning(
        "agent memory record turn failed agent_id=%s session_id=%s run_id=%s error=%s",
        agent_id,
        session_id,
        run_id,
        exc,
    )
    common = {
        "run_id": run_id,
        "request_id": request_id,
        "strategy": memory_capsule_strategy,
        "error": str(exc),
        **error_facts,
    }
    emit_memory_event(
        session_id=session_id,
        event_type="memory.write.failed",
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload={**common, "patch_id": str(patch_id_hint or "")},
    )
    emit_memory_event(
        session_id=session_id,
        event_type="memory.turn.record_failed",
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
        attach_id=attach_id or None,
        payload=common,
    )


__all__ = [
    "MEMORY_CAPSULE_REFRESH_FAILED_CODE",
    "MEMORY_CAPSULE_REFRESH_FAILED_REASON",
    "MEMORY_CONTEXT_BUILD_FAILED_CODE",
    "MEMORY_CONTEXT_BUILD_FAILED_REASON",
    "MEMORY_FOLLOWUP_FAILED_CODE",
    "MEMORY_FOLLOWUP_FAILED_REASON",
    "derive_memory_patch_id",
    "emit_memory_write_events",
    "memory_error_facts",
    "record_memory_failure",
    "text_fingerprint",
]
