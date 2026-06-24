import hashlib
import logging
from typing import Any, Callable

from openminion.modules.memory.errors import MemctlError
from openminion.services.constants import MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE


MemoryEventEmitter = Callable[..., None]

MEMORY_CONTEXT_BUILD_FAILED_CODE = "MEMORY_CONTEXT_BUILD_FAILED"
MEMORY_CONTEXT_BUILD_FAILED_REASON = "memory_context_build_failed"
MEMORY_WRITE_FAILED_CODE = "MEMORY_WRITE_FAILED"
MEMORY_WRITE_FAILED_REASON = "memory_write_failed"
MEMORY_CAPSULE_REFRESH_FAILED_CODE = "MEMORY_CAPSULE_REFRESH_FAILED"
MEMORY_CAPSULE_REFRESH_FAILED_REASON = "memory_capsule_refresh_failed"


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


def _text_fingerprint(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _maybe_derive_patch_id(
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
    try:
        return str(
            derive_patch_id(
                session_id=session_id,
                run_id=run_id,
                request_id=request_id,
                user_message=user_message,
            )
            or ""
        )
    except Exception:
        return ""


def _emit_memory_write_events(
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


def _refresh_capsule_after_write(
    *,
    agent_memory: Any,
    logger: logging.Logger,
    agent_id: str,
    memory_capsule_cache: dict[str, str],
    session_id: str,
    run_id: str,
    request_id: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    emit_memory_event: MemoryEventEmitter,
    outbound_metadata: dict[str, str],
) -> None:
    prior_capsule = memory_capsule_cache.get(session_id, "")
    try:
        refreshed_capsule = agent_memory.build_context(
            session_id=session_id,
            user_message="",
        )
        memory_capsule_cache[session_id] = refreshed_capsule
        outbound_metadata["memory_capsule_refreshed"] = "true"
        emit_memory_event(
            session_id=session_id,
            event_type="memory.capsule.refreshed",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={
                "run_id": run_id,
                "request_id": request_id,
                "reason": "on_write",
                "before_chars": str(len(prior_capsule)),
                "after_chars": str(len(refreshed_capsule)),
                "before_fingerprint": _text_fingerprint(prior_capsule),
                "after_fingerprint": _text_fingerprint(refreshed_capsule),
                "changed": str(prior_capsule != refreshed_capsule).lower(),
            },
        )
    except Exception as exc:
        error_facts = memory_error_facts(
            exc,
            fallback_code=MEMORY_CAPSULE_REFRESH_FAILED_CODE,
            fallback_reason=MEMORY_CAPSULE_REFRESH_FAILED_REASON,
        )
        logger.warning(
            "agent memory capsule refresh failed agent_id=%s session_id=%s run_id=%s error=%s",
            agent_id,
            session_id,
            run_id,
            exc,
        )
        outbound_metadata["memory_capsule_refreshed"] = "false"
        outbound_metadata["memory_capsule_refresh_error_code"] = error_facts[
            "error_code"
        ]
        outbound_metadata["memory_capsule_refresh_reason_code"] = error_facts[
            "reason_code"
        ]
        emit_memory_event(
            session_id=session_id,
            event_type="memory.capsule.refresh_failed",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={
                "run_id": run_id,
                "request_id": request_id,
                "reason": "on_write",
                "error": str(exc),
                **error_facts,
            },
        )


def _maybe_checkpoint_summary(
    *,
    agent_memory: Any,
    logger: logging.Logger,
    agent_id: str,
    session_id: str,
    run_id: str,
) -> None:
    checkpoint_summary = getattr(agent_memory, "maybe_checkpoint_session_summary", None)
    if not callable(checkpoint_summary):
        return
    try:
        checkpoint_summary(session_id)
    except Exception as exc:
        logger.warning(
            "agent session summary checkpoint failed agent_id=%s session_id=%s run_id=%s error=%s",
            agent_id,
            session_id,
            run_id,
            exc,
        )


def record_memory_turn(
    *,
    agent_memory: Any,
    logger: logging.Logger,
    agent_id: str,
    memory_capsule_strategy: str,
    memory_capsule_cache: dict[str, str],
    session_id: str,
    run_id: str,
    request_id: str,
    channel: str,
    target: str,
    user_message: str,
    assistant_message: str,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    emit_memory_event: MemoryEventEmitter,
    outbound_metadata: dict[str, str],
) -> None:
    patch_id_hint = ""
    try:
        patch_id_hint = _maybe_derive_patch_id(
            agent_memory=agent_memory,
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            user_message=user_message,
        )
        emit_memory_event(
            session_id=session_id,
            event_type="memory.write.started",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={
                "run_id": run_id,
                "request_id": request_id,
                "strategy": memory_capsule_strategy,
                "patch_id": patch_id_hint,
            },
        )
        memory_patch = agent_memory.record_turn(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            channel=channel,
            target=target,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        outbound_metadata["memory_enabled"] = "true"
        outbound_metadata["memory_facts_added"] = str(memory_patch.facts_added)
        outbound_metadata["memory_todos_added"] = str(memory_patch.todos_added)
        outbound_metadata["memory_todos_completed"] = str(memory_patch.todos_completed)
        outbound_metadata["memory_patch_id"] = str(memory_patch.patch_id or "")
        patch_changed = (
            memory_patch.facts_added > 0
            or memory_patch.todos_added > 0
            or memory_patch.todos_completed > 0
        )
        _emit_memory_write_events(
            emit_memory_event=emit_memory_event,
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            run_id=run_id,
            request_id=request_id,
            memory_capsule_strategy=memory_capsule_strategy,
            patch_id_hint=patch_id_hint,
            memory_patch=memory_patch,
            patch_changed=patch_changed,
        )

        if (
            memory_capsule_strategy == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE
            and patch_changed
        ):
            _refresh_capsule_after_write(
                agent_memory=agent_memory,
                logger=logger,
                agent_id=agent_id,
                memory_capsule_cache=memory_capsule_cache,
                session_id=session_id,
                run_id=run_id,
                request_id=request_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                attach_id=attach_id,
                emit_memory_event=emit_memory_event,
                outbound_metadata=outbound_metadata,
            )
        elif memory_capsule_strategy == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE:
            emit_memory_event(
                session_id=session_id,
                event_type="memory.capsule.refresh_skipped",
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
                payload={
                    "run_id": run_id,
                    "request_id": request_id,
                    "reason": "no_memory_change",
                },
            )
    except Exception as exc:
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
        emit_memory_event(
            session_id=session_id,
            event_type="memory.write.failed",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={
                "run_id": run_id,
                "request_id": request_id,
                "strategy": memory_capsule_strategy,
                "patch_id": str(patch_id_hint or ""),
                "error": str(exc),
                **error_facts,
            },
        )
        emit_memory_event(
            session_id=session_id,
            event_type="memory.turn.record_failed",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={
                "run_id": run_id,
                "request_id": request_id,
                "strategy": memory_capsule_strategy,
                "error": str(exc),
                **error_facts,
            },
        )
    finally:
        _maybe_checkpoint_summary(
            agent_memory=agent_memory,
            logger=logger,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
        )
