from __future__ import annotations

import concurrent.futures
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.modules.a2a.constants import (
    A2A_ACTIVE_JOB_STATES,
    A2A_AGENT_STATUS_ONLINE,
    A2A_AUDIT_STATUS_CALL_RECEIVED,
    A2A_AUDIT_STATUS_CANCELED,
    A2A_AUDIT_STATUS_FAILED,
    A2A_AUDIT_STATUS_JOB_QUEUED,
    A2A_AUDIT_STATUS_RECOVERY_FAILED,
    A2A_AUDIT_STATUS_RUNNING,
    A2A_AUDIT_STATUS_SUCCESS,
    A2A_IDEMPOTENCY_STATUS_CANCELED,
    A2A_IDEMPOTENCY_STATUS_FAILED,
    A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
    A2A_IDEMPOTENCY_STATUS_SUCCESS,
    A2A_JOB_STATE_CANCELED,
    A2A_JOB_STATE_FAILED,
    A2A_JOB_STATE_PENDING,
    A2A_JOB_STATE_RUNNING,
    A2A_JOB_STATE_SUCCESS,
    A2A_POLICY_ACTION_ALLOW,
)
from openminion.modules.a2a.artifacts import LocalArtifactStore
from openminion.modules.a2a.errors import (
    A2AError,
    ERROR_CODE_ALREADY_COMPLETED,
    ERROR_CODE_CANCELED,
    ERROR_CODE_FAILED,
    ERROR_CODE_HANDLER_ERROR,
    ERROR_CODE_IN_PROGRESS,
    ERROR_CODE_INVALID_ARGUMENT,
    ERROR_CODE_JOB_FAILED,
    ERROR_CODE_JOB_NOT_FOUND,
    ERROR_CODE_POLICY_DENIED,
    ERROR_CODE_STALE_JOB,
)
from openminion.modules.a2a.models import (
    AuditRecord,
    Envelope,
    EnvelopeValidationError,
    JobRecord,
    validate_envelope_contract,
    iso_now,
    new_uuid,
)
from openminion.modules.a2a.policy import PolicyEngine
from openminion.modules.a2a.registry import AgentHandler, AgentRegistry
from openminion.modules.a2a.storage.base import AuditStore, StateStore
from openminion.modules.a2a.interfaces import A2A_INTERFACE_VERSION


class A2ARuntime:
    contract_version = A2A_INTERFACE_VERSION

    def __init__(
        self,
        *,
        state_store: StateStore,
        audit_store: AuditStore,
        artifact_store: LocalArtifactStore | None = None,
        policy_engine: PolicyEngine | None = None,
        max_inline_bytes: int = 16_384,
        recovery_stale_heartbeat_sec: int = 300,
        max_workers: int = 8,
    ) -> None:
        self.state_store = state_store
        self.audit_store = audit_store
        self.artifact_store = artifact_store
        self.policy = policy_engine or PolicyEngine(
            default_action=A2A_POLICY_ACTION_ALLOW
        )
        self.max_inline_bytes = int(max_inline_bytes)
        self.recovery_stale_heartbeat_sec = int(recovery_stale_heartbeat_sec)

        self.registry = AgentRegistry(state_store)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, concurrent.futures.Future[Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

        self.recover_stale_jobs()

    def register_agent(
        self,
        agent_id: str,
        capabilities: list[str],
        handler: AgentHandler,
        *,
        tags: list[str] | None = None,
    ) -> None:
        descriptor = self._build_descriptor(
            agent_id=agent_id, capabilities=capabilities, tags=tags
        )
        self.registry.register(descriptor, handler)

    def list_agents(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.registry.list_agents()]

    def call(self, envelope: Envelope) -> Envelope:
        if envelope.type != "call":
            raise A2AError(
                ERROR_CODE_INVALID_ARGUMENT, "Envelope type must be 'call' for call()"
            )
        self._validate_envelope(envelope)

        route = self.registry.resolve(envelope)
        resolved_agent = route.descriptor.agent_id
        if not self.policy.is_allowed(envelope, resolved_agent):
            raise A2AError(
                ERROR_CODE_POLICY_DENIED,
                "Policy denied call",
                {"agent": resolved_agent, "method": envelope.method},
            )

        scope = self._idempotency_scope(envelope, resolved_agent)
        reserved, existing = self.state_store.reserve_idempotency(
            envelope.idempotency_key,
            scope,
            stale_reclaim_after_sec=self.recovery_stale_heartbeat_sec,
        )
        if not reserved and existing is not None:
            return self._result_from_idempotency(
                existing=existing,
                request=envelope,
                resolved_agent=resolved_agent,
            )

        self._audit_call_received(envelope, resolved_agent=resolved_agent)
        try:
            return self._execute_call_handler(
                envelope=envelope,
                resolved_agent=resolved_agent,
                handler=route.handler,
                scope=scope,
            )
        except A2AError as exc:
            return self._call_error_result(
                envelope=envelope,
                resolved_agent=resolved_agent,
                scope=scope,
                error=exc.to_dict(),
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception as exc:  # noqa: BLE001
            error = {"code": ERROR_CODE_HANDLER_ERROR, "message": str(exc)}
            return self._call_error_result(
                envelope=envelope,
                resolved_agent=resolved_agent,
                scope=scope,
                error=error,
                error_code=ERROR_CODE_HANDLER_ERROR,
                error_message=str(exc),
            )

    def job_start(self, envelope: Envelope) -> str:
        if envelope.type != "job.start":
            raise A2AError(
                ERROR_CODE_INVALID_ARGUMENT,
                "Envelope type must be 'job.start' for job_start()",
            )
        self._validate_envelope(envelope)

        route = self.registry.resolve(envelope)
        if not self.policy.is_allowed(envelope, route.descriptor.agent_id):
            raise A2AError(
                ERROR_CODE_POLICY_DENIED,
                "Policy denied job start",
                {"agent": route.descriptor.agent_id, "method": envelope.method},
            )

        scope = self._idempotency_scope(envelope, route.descriptor.agent_id)
        reserved, existing = self.state_store.reserve_idempotency(
            envelope.idempotency_key, scope
        )
        if not reserved and existing is not None:
            if existing.task_id:
                return existing.task_id
            if existing.status == A2A_IDEMPOTENCY_STATUS_SUCCESS:
                raise A2AError(
                    ERROR_CODE_ALREADY_COMPLETED,
                    "Job already completed for this idempotency key",
                    {"idempotency_key": envelope.idempotency_key},
                )
            raise A2AError(
                ERROR_CODE_IN_PROGRESS,
                "Job is already in progress for this idempotency key",
                {"idempotency_key": envelope.idempotency_key},
            )

        task_id = new_uuid()
        now = iso_now()
        job = JobRecord(
            task_id=task_id,
            trace_id=envelope.trace_id,
            idempotency_key=envelope.idempotency_key,
            agent_id=route.descriptor.agent_id,
            method=envelope.method,
            state=A2A_JOB_STATE_PENDING,
            current_step="queued",
            progress=0.0,
            created_at=now,
            updated_at=now,
            heartbeat_at=now,
        )
        self.state_store.create_job(job)
        self.state_store.set_idempotency_result(
            envelope.idempotency_key,
            scope,
            A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
            task_id=task_id,
        )

        self._append_audit_record(
            envelope=envelope,
            resolved_agent=route.descriptor.agent_id,
            status=A2A_AUDIT_STATUS_JOB_QUEUED,
            task_id=task_id,
            payload=envelope.to_dict(),
        )

        cancel_event = threading.Event()
        future = self._executor.submit(
            self._run_job,
            envelope,
            route.descriptor.agent_id,
            route.handler,
            task_id,
            scope,
            cancel_event,
        )

        with self._lock:
            self._futures[task_id] = future
            self._cancel_events[task_id] = cancel_event

        return task_id

    def job_status(self, task_id: str) -> JobRecord:
        job = self.state_store.get_job(task_id)
        if job is None:
            raise A2AError(ERROR_CODE_JOB_NOT_FOUND, f"Job '{task_id}' was not found")
        return job

    def job_cancel(self, task_id: str) -> JobRecord:
        job = self.state_store.get_job(task_id)
        if job is None:
            raise A2AError(ERROR_CODE_JOB_NOT_FOUND, f"Job '{task_id}' was not found")
        if job.is_terminal():
            return job

        with self._lock:
            cancel_event = self._cancel_events.get(task_id)
            future = self._futures.get(task_id)

        if cancel_event is not None:
            cancel_event.set()
        if future is not None:
            future.cancel()

        error = {"code": ERROR_CODE_CANCELED, "message": "Job canceled"}
        updated = self.state_store.update_job(
            task_id,
            self._job_patch(
                state=A2A_JOB_STATE_CANCELED,
                current_step="canceled",
                error=error,
            ),
        )

        scope = f"job.start:{updated.agent_id}:{updated.method}"
        self.state_store.set_idempotency_result(
            updated.idempotency_key,
            scope,
            A2A_IDEMPOTENCY_STATUS_CANCELED,
            error=error,
            task_id=task_id,
        )

        self._append_runtime_audit(
            trace_id=updated.trace_id,
            resolved_agent=updated.agent_id,
            method=updated.method,
            status=A2A_AUDIT_STATUS_CANCELED,
            audit_type="job.cancel",
            task_id=task_id,
            error_code=ERROR_CODE_CANCELED,
            error_message="Job canceled",
            data=updated.to_dict(),
        )
        return updated

    def recover_stale_jobs(self) -> list[str]:
        stale: list[str] = []
        now = datetime.now(timezone.utc)
        rows = self.state_store.list_jobs(
            {"states": list(A2A_ACTIVE_JOB_STATES), "limit": 5000}
        )
        for row in rows:
            if not _is_stale(
                row.heartbeat_at,
                now=now,
                stale_after_sec=self.recovery_stale_heartbeat_sec,
            ):
                continue
            error = {
                "code": ERROR_CODE_STALE_JOB,
                "message": "Job marked failed during startup recovery due to stale heartbeat",
            }
            self.state_store.update_job(
                row.task_id,
                self._job_patch(
                    state=A2A_JOB_STATE_FAILED,
                    current_step="recovery_failed",
                    error=error,
                ),
            )
            scope = f"job.start:{row.agent_id}:{row.method}"
            self.state_store.set_idempotency_result(
                row.idempotency_key,
                scope,
                A2A_IDEMPOTENCY_STATUS_FAILED,
                error=error,
                task_id=row.task_id,
            )
            stale.append(row.task_id)
            self._append_runtime_audit(
                trace_id=row.trace_id,
                resolved_agent=row.agent_id,
                method=row.method,
                status=A2A_AUDIT_STATUS_RECOVERY_FAILED,
                task_id=row.task_id,
                error_code=ERROR_CODE_STALE_JOB,
                error_message=error["message"],
                data={"task_id": row.task_id},
            )
        return stale

    def query_trace(self, trace_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.audit_store.query_audit({"trace_id": trace_id, "limit": limit})
        return [row.to_dict() for row in rows]

    def query_errors(
        self, *, since_seconds: int = 3600, limit: int = 1000
    ) -> list[dict[str, Any]]:
        since_ts = (
            datetime.now(timezone.utc) - timedelta(seconds=max(0, since_seconds))
        ).isoformat()
        rows = self.audit_store.query_audit(
            {
                "since_ts": since_ts,
                "error_only": True,
                "limit": limit,
            }
        )
        return [row.to_dict() for row in rows]

    def close(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)
        self.state_store.close()
        self.audit_store.close()

    def _run_job(
        self,
        envelope: Envelope,
        resolved_agent: str,
        handler: AgentHandler,
        task_id: str,
        scope: str,
        cancel_event: threading.Event,
    ) -> None:
        try:
            self._mark_job_running(
                envelope=envelope, resolved_agent=resolved_agent, task_id=task_id
            )
            if cancel_event.is_set():
                return
            result = handler(envelope)
            if cancel_event.is_set():
                return
            current = self.state_store.get_job(task_id)
            if current is None or current.state == A2A_JOB_STATE_CANCELED:
                return
            self._mark_job_success(
                envelope=envelope,
                resolved_agent=resolved_agent,
                task_id=task_id,
                scope=scope,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_job_failed(
                envelope=envelope,
                resolved_agent=resolved_agent,
                task_id=task_id,
                scope=scope,
                exc=exc,
            )
        finally:
            with self._lock:
                self._futures.pop(task_id, None)
                self._cancel_events.pop(task_id, None)

    def _audit_call_received(self, envelope: Envelope, *, resolved_agent: str) -> None:
        self._append_audit_record(
            envelope=envelope,
            resolved_agent=resolved_agent,
            status=A2A_AUDIT_STATUS_CALL_RECEIVED,
            payload=envelope.to_dict(),
        )

    def _execute_call_handler(
        self,
        *,
        envelope: Envelope,
        resolved_agent: str,
        handler: AgentHandler,
        scope: str,
    ) -> Envelope:
        result = handler(envelope)
        result_inline, result_ref = self._store_result_if_needed(
            result, label=envelope.method
        )
        self.state_store.set_idempotency_result(
            envelope.idempotency_key,
            scope,
            A2A_IDEMPOTENCY_STATUS_SUCCESS,
            result_inline=result_inline,
            result_ref=result_ref,
        )
        response = self._success_result_envelope(
            request=envelope,
            resolved_agent=resolved_agent,
            result_inline=result_inline,
            result_ref=result_ref,
            cached=False,
        )
        self._append_audit_record(
            envelope=response,
            resolved_agent=response.to_agent,
            status=A2A_AUDIT_STATUS_SUCCESS,
            payload=response.to_dict(),
        )
        return response

    def _call_error_result(
        self,
        *,
        envelope: Envelope,
        resolved_agent: str,
        scope: str,
        error: dict[str, Any],
        error_code: str,
        error_message: str,
    ) -> Envelope:
        self.state_store.set_idempotency_result(
            envelope.idempotency_key,
            scope,
            A2A_IDEMPOTENCY_STATUS_FAILED,
            error=error,
        )
        response = self._error_result_envelope(
            request=envelope,
            resolved_agent=resolved_agent,
            error=error,
        )
        self._append_audit_record(
            envelope=response,
            resolved_agent=response.to_agent,
            status=A2A_AUDIT_STATUS_FAILED,
            error_code=error_code,
            error_message=error_message,
            payload=response.to_dict(),
        )
        return response

    def _mark_job_running(
        self, *, envelope: Envelope, resolved_agent: str, task_id: str
    ) -> None:
        self.state_store.update_job(
            task_id,
            self._job_patch(
                state=A2A_JOB_STATE_RUNNING,
                current_step="executing",
                progress=0.1,
            ),
        )
        self._append_runtime_audit(
            trace_id=envelope.trace_id,
            resolved_agent=resolved_agent,
            method=envelope.method,
            status=A2A_AUDIT_STATUS_RUNNING,
            task_id=task_id,
            to_capability=envelope.to_capability,
        )

    def _mark_job_success(
        self,
        *,
        envelope: Envelope,
        resolved_agent: str,
        task_id: str,
        scope: str,
        result: Envelope,
    ) -> None:
        result_inline, result_ref = self._store_result_if_needed(
            result, label=envelope.method
        )
        self.state_store.update_job(
            task_id,
            self._job_patch(
                state=A2A_JOB_STATE_SUCCESS,
                current_step="done",
                progress=1.0,
                result_inline=result_inline,
                result_ref=result_ref,
                error=None,
            ),
        )
        self.state_store.set_idempotency_result(
            envelope.idempotency_key,
            scope,
            A2A_IDEMPOTENCY_STATUS_SUCCESS,
            result_inline=result_inline,
            result_ref=result_ref,
            task_id=task_id,
        )
        self._append_runtime_audit(
            trace_id=envelope.trace_id,
            resolved_agent=resolved_agent,
            method=envelope.method,
            status=A2A_AUDIT_STATUS_SUCCESS,
            task_id=task_id,
            to_capability=envelope.to_capability,
            data={"result_ref": result_ref},
        )

    def _mark_job_failed(
        self,
        *,
        envelope: Envelope,
        resolved_agent: str,
        task_id: str,
        scope: str,
        exc: Exception,
    ) -> None:
        error = {"code": ERROR_CODE_JOB_FAILED, "message": str(exc)}
        try:
            current = self.state_store.get_job(task_id)
            if current is not None and current.state != A2A_JOB_STATE_CANCELED:
                self.state_store.update_job(
                    task_id,
                    self._job_patch(
                        state=A2A_JOB_STATE_FAILED,
                        current_step="failed",
                        error=error,
                    ),
                )
                self.state_store.set_idempotency_result(
                    envelope.idempotency_key,
                    scope,
                    A2A_IDEMPOTENCY_STATUS_FAILED,
                    error=error,
                    task_id=task_id,
                )
        finally:
            self._append_runtime_audit(
                trace_id=envelope.trace_id,
                resolved_agent=resolved_agent,
                method=envelope.method,
                status=A2A_AUDIT_STATUS_FAILED,
                task_id=task_id,
                to_capability=envelope.to_capability,
                error_code=ERROR_CODE_JOB_FAILED,
                error_message=str(exc),
            )

    def _idempotency_scope(self, envelope: Envelope, resolved_agent: str) -> str:
        return f"{envelope.type}:{resolved_agent}:{envelope.method}"

    def _result_from_idempotency(
        self, *, existing: Any, request: Envelope, resolved_agent: str
    ) -> Envelope:
        if existing.status == A2A_IDEMPOTENCY_STATUS_SUCCESS:
            return self._success_result_envelope(
                request=request,
                resolved_agent=resolved_agent,
                result_inline=existing.result_inline,
                result_ref=existing.result_ref,
                cached=True,
            )

        if existing.status in {
            A2A_IDEMPOTENCY_STATUS_FAILED,
            A2A_IDEMPOTENCY_STATUS_CANCELED,
        }:
            fallback_code = (
                ERROR_CODE_FAILED
                if existing.status == A2A_IDEMPOTENCY_STATUS_FAILED
                else ERROR_CODE_CANCELED
            )
            return self._error_result_envelope(
                request=request,
                resolved_agent=resolved_agent,
                error=existing.error
                or {"code": fallback_code, "message": "Cached failure"},
                cached=True,
                task_id=existing.task_id,
            )

        return self._in_progress_result_envelope(
            request=request,
            resolved_agent=resolved_agent,
            task_id=existing.task_id,
            cached=True,
        )

    def _success_result_envelope(
        self,
        *,
        request: Envelope,
        resolved_agent: str,
        result_inline: dict[str, Any] | None,
        result_ref: str | None,
        cached: bool,
    ) -> Envelope:
        data: dict[str, Any] = {}
        if result_inline is not None:
            data = result_inline
        elif result_ref:
            data = {"result_ref": result_ref}
        return Envelope.new(
            from_agent=resolved_agent,
            to_agent=request.from_agent,
            to_capability=None,
            type="result",
            method=request.method,
            params={
                "ok": True,
                "status": A2A_IDEMPOTENCY_STATUS_SUCCESS,
                "cached": cached,
                "data": data,
            },
            timeout_ms=request.timeout_ms,
            idempotency_key=request.idempotency_key,
            trace_id=request.trace_id,
            meta={"cached": cached},
        )

    def _error_result_envelope(
        self,
        *,
        request: Envelope,
        resolved_agent: str,
        error: dict[str, Any],
        cached: bool = False,
        task_id: str | None = None,
    ) -> Envelope:
        params = {
            "ok": False,
            "status": error.get("code", ERROR_CODE_FAILED),
            "cached": cached,
            "error": error,
        }
        if task_id:
            params["task_id"] = task_id
        return Envelope.new(
            from_agent=resolved_agent,
            to_agent=request.from_agent,
            to_capability=None,
            type="result",
            method=request.method,
            params=params,
            timeout_ms=request.timeout_ms,
            idempotency_key=request.idempotency_key,
            trace_id=request.trace_id,
            meta={"cached": cached, "error": True},
        )

    def _in_progress_result_envelope(
        self,
        *,
        request: Envelope,
        resolved_agent: str,
        task_id: str | None,
        cached: bool,
    ) -> Envelope:
        data: dict[str, Any] = {}
        if task_id is not None:
            data["task_id"] = task_id
        params = {
            "ok": False,
            "status": ERROR_CODE_IN_PROGRESS,
            "cached": cached,
            "data": data,
        }
        if task_id is not None:
            params["task_id"] = task_id
        return Envelope.new(
            from_agent=resolved_agent,
            to_agent=request.from_agent,
            to_capability=None,
            type="result",
            method=request.method,
            params=params,
            timeout_ms=request.timeout_ms,
            idempotency_key=request.idempotency_key,
            trace_id=request.trace_id,
            meta={"cached": cached, "in_progress": True},
        )

    def _build_descriptor(
        self, *, agent_id: str, capabilities: list[str], tags: list[str] | None
    ) -> Any:
        from openminion.modules.a2a.models import AgentDescriptor

        return AgentDescriptor(
            agent_id=agent_id,
            capabilities=list(capabilities),
            endpoint=f"inproc://{agent_id}",
            tags=list(tags or []),
            status=A2A_AGENT_STATUS_ONLINE,
        )

    def _store_result_if_needed(
        self, result: dict[str, Any], *, label: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        encoded = json.dumps(result, ensure_ascii=True).encode("utf-8")
        if len(encoded) <= self.max_inline_bytes or self.artifact_store is None:
            return result, None
        artifact = self.artifact_store.put_bytes(
            encoded, mime="application/json", label=label
        )
        return None, artifact.ref

    def _validate_envelope(self, envelope: Envelope) -> None:
        try:
            validate_envelope_contract(envelope)
        except EnvelopeValidationError as exc:
            raise A2AError(ERROR_CODE_INVALID_ARGUMENT, str(exc)) from exc

    def _append_audit(self, record: AuditRecord) -> None:
        try:
            self.audit_store.append_audit(record)
        except Exception:
            return

    def _job_patch(self, **patch: Any) -> dict[str, Any]:
        stamped = iso_now()
        return {"heartbeat_at": stamped, "updated_at": stamped, **patch}

    def _append_audit_record(
        self,
        *,
        envelope: Envelope,
        resolved_agent: str | None,
        status: str,
        task_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._append_audit(
            AuditRecord(
                ts=iso_now(),
                msg_id=envelope.msg_id,
                trace_id=envelope.trace_id,
                from_agent=envelope.from_agent,
                to_agent=resolved_agent,
                to_capability=envelope.to_capability,
                type=envelope.type,
                method=envelope.method,
                status=status,
                task_id=task_id,
                error_code=error_code,
                error_message=error_message,
                envelope=payload,
            )
        )

    def _append_runtime_audit(
        self,
        *,
        trace_id: str,
        resolved_agent: str,
        method: str,
        status: str,
        task_id: str,
        audit_type: str = "job.status",
        to_capability: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._append_audit(
            AuditRecord(
                ts=iso_now(),
                msg_id=new_uuid(),
                trace_id=trace_id,
                from_agent="runtime",
                to_agent=resolved_agent,
                to_capability=to_capability,
                type=audit_type,
                method=method,
                status=status,
                task_id=task_id,
                error_code=error_code,
                error_message=error_message,
                data=data,
            )
        )


def _is_stale(heartbeat_at: str, *, now: datetime, stale_after_sec: int) -> bool:
    try:
        heartbeat = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    heartbeat = heartbeat.astimezone(timezone.utc)
    return (now - heartbeat).total_seconds() > max(1, stale_after_sec)
