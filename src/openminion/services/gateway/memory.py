import hashlib
import logging
import threading
from collections import deque
from dataclasses import dataclass
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
MEMORY_FOLLOWUP_FAILED_CODE = "MEMORY_FOLLOWUP_FAILED"
MEMORY_FOLLOWUP_FAILED_REASON = "memory_followup_failed"


@dataclass(frozen=True)
class MemoryFollowupJob:
    agent_memory: Any
    logger: logging.Logger
    agent_id: str
    memory_capsule_strategy: str
    memory_capsule_cache: dict[str, str]
    session_id: str
    run_id: str
    request_id: str
    conversation_id: str
    thread_id: str
    attach_id: str
    emit_memory_event: MemoryEventEmitter
    outbound_metadata: dict[str, str]
    patch_changed: bool


class MemoryFollowupQueue:
    def __init__(self, *, auto_start: bool = True) -> None:
        self._auto_start = auto_start
        self._pending: deque[MemoryFollowupJob] = deque()
        self._active_by_session: dict[str, int] = {}
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None

    def enqueue(self, job: MemoryFollowupJob) -> None:
        with self._condition:
            self._pending.append(job)
            if self._auto_start and not self._worker_is_alive():
                self._worker = threading.Thread(
                    target=self._drain,
                    name="openminion-memory-followup",
                    daemon=True,
                )
                self._worker.start()
            self._condition.notify_all()

    def flush(self, *, session_id: str | None = None) -> None:
        while True:
            with self._condition:
                job = self._take_locked(session_id=session_id)
                if job is None:
                    if self._active_count_locked(session_id=session_id) <= 0:
                        return
                    self._condition.wait(timeout=0.05)
                    continue
            self._run(job)

    def pending_count(self, *, session_id: str | None = None) -> int:
        with self._condition:
            if session_id is None:
                return len(self._pending)
            return sum(1 for job in self._pending if job.session_id == session_id)

    def _worker_is_alive(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _active_count_locked(self, *, session_id: str | None) -> int:
        if session_id is None:
            return sum(self._active_by_session.values())
        return int(self._active_by_session.get(session_id, 0))

    def _take_locked(self, *, session_id: str | None) -> MemoryFollowupJob | None:
        for index, job in enumerate(self._pending):
            if session_id is not None and job.session_id != session_id:
                continue
            del self._pending[index]
            self._active_by_session[job.session_id] = (
                self._active_by_session.get(job.session_id, 0) + 1
            )
            return job
        return None

    def _drain(self) -> None:
        while True:
            with self._condition:
                job = self._take_locked(session_id=None)
                if job is None:
                    self._condition.notify_all()
                    return
            self._run(job)

    def _run(self, job: MemoryFollowupJob) -> None:
        try:
            run_memory_followup(job)
        finally:
            with self._condition:
                active = max(0, self._active_by_session.get(job.session_id, 1) - 1)
                if active:
                    self._active_by_session[job.session_id] = active
                else:
                    self._active_by_session.pop(job.session_id, None)
                self._condition.notify_all()


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


def _has_followup_work(job: MemoryFollowupJob) -> bool:
    if (
        job.memory_capsule_strategy == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE
        and job.patch_changed
    ):
        return True
    checkpoint_summary = getattr(
        job.agent_memory, "maybe_checkpoint_session_summary", None
    )
    return callable(checkpoint_summary)


def _emit_followup_pending(job: MemoryFollowupJob) -> None:
    refresh_pending = (
        job.memory_capsule_strategy == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE
        and job.patch_changed
    )
    checkpoint_pending = callable(
        getattr(job.agent_memory, "maybe_checkpoint_session_summary", None)
    )
    if refresh_pending:
        job.outbound_metadata["memory_capsule_refreshed"] = "pending"
        job.outbound_metadata["memory_capsule_refresh_pending"] = "true"
    if checkpoint_pending:
        job.outbound_metadata["memory_summary_checkpoint_pending"] = "true"
    job.outbound_metadata["memory_followup_deferred"] = "true"
    job.emit_memory_event(
        session_id=job.session_id,
        event_type="memory.followup.pending",
        conversation_id=job.conversation_id or None,
        thread_id=job.thread_id or None,
        attach_id=job.attach_id or None,
        payload={
            "run_id": job.run_id,
            "request_id": job.request_id,
            "capsule_refresh": str(refresh_pending).lower(),
            "summary_checkpoint": str(checkpoint_pending).lower(),
        },
    )


def run_memory_followup(job: MemoryFollowupJob) -> None:
    try:
        if (
            job.memory_capsule_strategy == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE
            and job.patch_changed
        ):
            _refresh_capsule_after_write(
                agent_memory=job.agent_memory,
                logger=job.logger,
                agent_id=job.agent_id,
                memory_capsule_cache=job.memory_capsule_cache,
                session_id=job.session_id,
                run_id=job.run_id,
                request_id=job.request_id,
                conversation_id=job.conversation_id,
                thread_id=job.thread_id,
                attach_id=job.attach_id,
                emit_memory_event=job.emit_memory_event,
                outbound_metadata=job.outbound_metadata,
            )
        elif job.memory_capsule_strategy == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE:
            job.emit_memory_event(
                session_id=job.session_id,
                event_type="memory.capsule.refresh_skipped",
                conversation_id=job.conversation_id or None,
                thread_id=job.thread_id or None,
                attach_id=job.attach_id or None,
                payload={
                    "run_id": job.run_id,
                    "request_id": job.request_id,
                    "reason": "no_memory_change",
                },
            )
        _maybe_checkpoint_summary(
            agent_memory=job.agent_memory,
            logger=job.logger,
            agent_id=job.agent_id,
            session_id=job.session_id,
            run_id=job.run_id,
        )
        job.emit_memory_event(
            session_id=job.session_id,
            event_type="memory.followup.completed",
            conversation_id=job.conversation_id or None,
            thread_id=job.thread_id or None,
            attach_id=job.attach_id or None,
            payload={
                "run_id": job.run_id,
                "request_id": job.request_id,
                "capsule_refresh": str(
                    job.memory_capsule_strategy
                    == MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE
                    and job.patch_changed
                ).lower(),
            },
        )
    except Exception as exc:
        error_facts = memory_error_facts(
            exc,
            fallback_code=MEMORY_FOLLOWUP_FAILED_CODE,
            fallback_reason=MEMORY_FOLLOWUP_FAILED_REASON,
        )
        job.logger.warning(
            "agent memory followup failed agent_id=%s session_id=%s run_id=%s error=%s",
            job.agent_id,
            job.session_id,
            job.run_id,
            exc,
        )
        job.emit_memory_event(
            session_id=job.session_id,
            event_type="memory.followup.failed",
            conversation_id=job.conversation_id or None,
            thread_id=job.thread_id or None,
            attach_id=job.attach_id or None,
            payload={
                "run_id": job.run_id,
                "request_id": job.request_id,
                "error": str(exc),
                **error_facts,
            },
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
    followup_queue: MemoryFollowupQueue | None = None,
    defer_followup: bool = False,
) -> None:
    patch_id_hint = ""
    patch_changed = False
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
        followup_job = MemoryFollowupJob(
            agent_memory=agent_memory,
            logger=logger,
            agent_id=agent_id,
            memory_capsule_strategy=memory_capsule_strategy,
            memory_capsule_cache=memory_capsule_cache,
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            emit_memory_event=emit_memory_event,
            outbound_metadata=outbound_metadata,
            patch_changed=patch_changed,
        )
        if (
            defer_followup
            and followup_queue is not None
            and _has_followup_work(followup_job)
        ):
            _emit_followup_pending(followup_job)
            followup_queue.enqueue(followup_job)
            return
        run_memory_followup(followup_job)
