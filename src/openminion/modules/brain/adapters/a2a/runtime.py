from __future__ import annotations

import sys
import time
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_FAILED,
    BRAIN_JOB_STATUS_PENDING,
    BRAIN_JOB_STATUS_RUNNING,
)
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.a2a.constants import (
    A2A_JOB_STATE_CANCELED,
    A2A_JOB_STATE_PENDING,
    A2A_JOB_STATE_RUNNING,
    A2A_JOB_STATE_SUCCESS,
)
from openminion.base.config import resolve_data_root
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.brain.schemas import DelegationContext, DelegationResultSummary


def _delegate_result_summary(
    payload: dict[str, Any],
    *,
    fallback: str,
) -> str:
    for key in ("body", "message", "summary", "answer", "result", "output"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return fallback


def _typed_delegation_result_summary(value: Any) -> dict[str, Any] | None:
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        return DelegationResultSummary.model_validate(raw).model_dump(mode="json")
    except Exception:
        return None


class A2actlAdapter:
    """Adapter for Agent-to-Agent communication via openminion-a2a runtime."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        *,
        home_root: str | Path | None = None,
        config: Any = None,
        agent_id: str | None = None,
        env: EnvironmentConfig | None = None,
        runtime_resolver: Callable[[], Any | None] | None = None,
    ) -> None:
        self._home_root = (
            Path(home_root).expanduser().resolve(strict=False)
            if home_root is not None
            else None
        )
        self._config = config
        self._agent_id = str(agent_id).strip() if agent_id else ""
        self._env = env or self._build_env(config)
        self._runtime_resolver = runtime_resolver
        self._runtime = None
        self._builtins_registered = False
        self._configured_agents_registered: set[str] = set()

    @staticmethod
    def _build_env(config: Any) -> EnvironmentConfig:
        runtime_env = getattr(getattr(config, "runtime", None), "env", {})
        if not isinstance(runtime_env, Mapping):
            runtime_env = {}
        return resolve_environment_config(runtime_env=runtime_env)

    def register_agent(
        self,
        agent_id: str,
        capabilities: list[str],
        handler: Any,
        *,
        tags: list[str] | None = None,
    ) -> None:
        runtime = self._ensure_runtime()
        runtime.register_agent(agent_id, capabilities, handler, tags=tags)

    def call(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]:
        runtime = self._ensure_runtime()
        start = time.monotonic()

        target = str(command.get("target_agent_id", "")).strip() or None
        method = str(command.get("method", "")).strip()
        params = (
            command.get("params", {}) if isinstance(command.get("params"), dict) else {}
        )
        expect_async = bool(command.get("expect_async"))
        timeout_ms = int(command.get("timeout_ms") or 30000)

        idempotency_key = str(command.get("idempotency_key") or "").strip()
        if not idempotency_key:
            command_id = str(command.get("command_id") or "").strip()
            idempotency_key = command_id or f"a2a:{session_id}:{method}"

        from_agent = self._agent_id or str(
            command.get("from_agent") or session_id or "openminion"
        )

        try:
            from openminion.modules.a2a.models import (
                Envelope,
                MESSAGE_TYPE_CALL,
                MESSAGE_TYPE_JOB_START,
            )
            from openminion.modules.a2a.errors import A2AError, ERROR_CODE_IN_PROGRESS
        except Exception as exc:  # pragma: no cover - dependency missing
            return {
                "status": BRAIN_ACTION_STATUS_FAILED,
                "summary": f"A2A runtime unavailable: {exc}",
                "error": {"code": "A2A_RUNTIME_MISSING", "message": str(exc)},
            }

        envelope = Envelope.new(
            from_agent=from_agent,
            to_agent=target,
            to_capability=None,
            type=MESSAGE_TYPE_JOB_START if expect_async else MESSAGE_TYPE_CALL,
            method=method,
            params=params,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            meta={
                "session_id": str(session_id or "").strip(),
                "from_agent": from_agent,
            },
        )

        try:
            if expect_async:
                task_id = runtime.job_start(envelope)
                return {
                    "status": BRAIN_JOB_STATUS_RUNNING,
                    "task_id": task_id,
                    "poll_after_ms": 1000,
                    "summary": "Async A2A job started.",
                    "metrics": _metrics(start),
                }

            response = runtime.call(envelope)
            payload = response.params if isinstance(response.params, dict) else {}
            if payload.get("ok") is True:
                data = payload.get("data", {})
                normalized_data = data if isinstance(data, dict) else {}
                summary = _delegate_result_summary(
                    normalized_data,
                    fallback=(
                        f"A2A call completed: {response.from_agent}.{response.method}"
                    ),
                )
                return {
                    "status": BRAIN_ACTION_STATUS_SUCCESS,
                    "summary": summary,
                    "outputs": normalized_data,
                    "artifact_refs": [],
                    "memory_refs": [],
                    "metrics": _metrics(start),
                }

            status_code = str(payload.get("status") or "A2A_FAILED")
            if status_code == ERROR_CODE_IN_PROGRESS or payload.get("task_id"):
                return {
                    "status": BRAIN_JOB_STATUS_RUNNING,
                    "task_id": payload.get("task_id"),
                    "poll_after_ms": 1000,
                    "summary": "A2A job already in progress.",
                    "metrics": _metrics(start),
                }

            error = (
                payload.get("error") if isinstance(payload.get("error"), dict) else {}
            )
            return {
                "status": BRAIN_ACTION_STATUS_FAILED,
                "summary": str(error.get("message") or "A2A call failed"),
                "error": {
                    "code": str(error.get("code") or status_code),
                    "message": str(error.get("message") or "A2A call failed"),
                    "details": error.get("details")
                    if isinstance(error, dict)
                    else None,
                },
                "metrics": _metrics(start),
            }
        except A2AError as exc:
            return {
                "status": BRAIN_ACTION_STATUS_FAILED,
                "summary": str(exc.message),
                "error": {
                    "code": str(exc.code),
                    "message": str(exc.message),
                    "details": exc.details,
                },
                "metrics": _metrics(start),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": BRAIN_ACTION_STATUS_FAILED,
                "summary": f"A2A runtime error: {exc}",
                "error": {"code": "A2A_RUNTIME_ERROR", "message": str(exc)},
                "metrics": _metrics(start),
            }

    def poll_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]:
        del session_id, trace_id
        runtime = self._ensure_runtime()
        start = time.monotonic()
        try:
            job = runtime.job_status(str(task_id or "").strip())
            response = _job_record_to_response(job)
            response.setdefault("metrics", _metrics(start))
            return response
        except Exception as exc:  # noqa: BLE001
            return {
                "status": BRAIN_JOB_STATUS_FAILED,
                "summary": f"A2A job polling failed: {exc}",
                "error": {"code": "A2A_JOB_POLL_FAILED", "message": str(exc)},
                "metrics": _metrics(start),
            }

    def cancel_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]:
        del session_id, trace_id
        runtime = self._ensure_runtime()
        start = time.monotonic()
        try:
            job = runtime.job_cancel(str(task_id or "").strip())
            response = _job_record_to_response(job)
            response.setdefault("metrics", _metrics(start))
            return response
        except Exception as exc:  # noqa: BLE001
            return {
                "status": BRAIN_JOB_STATUS_FAILED,
                "summary": f"A2A job cancel failed: {exc}",
                "error": {"code": "A2A_JOB_CANCEL_FAILED", "message": str(exc)},
                "metrics": _metrics(start),
            }

    def _ensure_runtime(self):
        if self._runtime is not None:
            return self._runtime

        self._bootstrap_a2a_path()

        from openminion.modules.a2a.artifacts import LocalArtifactStore
        from openminion.modules.a2a.config import RuntimeConfig, load_config
        from openminion.modules.a2a.policy import PolicyEngine
        from openminion.modules.a2a.runtime import A2ARuntime
        from openminion.modules.a2a.storage import (
            MemoryAuditStore,
            MemoryStateStore,
            SQLiteAuditStore,
            SQLiteStateStore,
        )

        cfg = load_config(self._config) if self._config is not None else RuntimeConfig()

        if self._home_root is not None:
            data_root = resolve_data_root(
                self._home_root,
                data_root=str(self._env.get("OPENMINION_DATA_ROOT", "")).strip()
                or None,
            )
            base = data_root / "a2a"
            state_path: str | Path = base / "state.db"
            audit_root: str | Path = base / "audit"
            artifacts_root: str | Path = base / "artifacts"
        else:
            state_path = cfg.storage.state.path
            audit_root = cfg.storage.audit.root
            artifacts_root = cfg.artifacts.root

        state_backend = str(cfg.storage.state.backend).strip().lower()
        if state_backend == "memory":
            state_store = MemoryStateStore()
        elif state_backend == "sqlite":
            state_store = SQLiteStateStore(state_path)
        else:
            raise RuntimeError(
                f"Unsupported a2a state backend: {cfg.storage.state.backend}"
            )

        audit_backend = str(cfg.storage.audit.backend).strip().lower()
        if audit_backend in {"memory", "inmemory"}:
            audit_store = MemoryAuditStore()
        elif audit_backend in {"sqlite", "sqlite_rotated"}:
            audit_store = SQLiteAuditStore(
                audit_root, retention_days=cfg.storage.audit.retention_days
            )
        else:
            raise RuntimeError(
                f"Unsupported a2a audit backend: {cfg.storage.audit.backend}"
            )

        policy = PolicyEngine.from_config(cfg.policy.default_action, cfg.policy.rules)
        artifacts = LocalArtifactStore(artifacts_root)

        self._runtime = A2ARuntime(
            state_store=state_store,
            audit_store=audit_store,
            artifact_store=artifacts,
            policy_engine=policy,
            max_inline_bytes=cfg.artifacts.max_inline_bytes,
            recovery_stale_heartbeat_sec=cfg.recovery.stale_heartbeat_sec,
        )

        if not self._builtins_registered:
            self._register_builtin_agents()
        self._register_configured_agents()

        return self._runtime

    def _resolve_runtime_handle(self) -> Any | None:
        resolver = self._runtime_resolver
        if not callable(resolver):
            return None
        try:
            return resolver()
        except Exception:  # pragma: no cover - defensive callback seam
            return None

    def _bootstrap_a2a_path(self) -> None:
        if "openminion.modules.a2a" in sys.modules:
            return
        try:
            import openminion.modules.a2a  # noqa: F401

            return
        except ModuleNotFoundError:
            pass

        root = Path(__file__).resolve().parents[4]
        candidate = root / "openminion" / "src"
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

    def _register_builtin_agents(self) -> None:
        if self._builtins_registered:
            return
        runtime = self._runtime
        if runtime is None:
            return

        from openminion.modules.a2a.models import Envelope

        def echo_handler(envelope: Envelope) -> dict[str, Any]:
            return {
                "agent": "agent.echo",
                "method": envelope.method,
                "params": envelope.params,
                "trace_id": envelope.trace_id,
            }

        def worker_handler(envelope: Envelope) -> dict[str, Any]:
            params = envelope.params if isinstance(envelope.params, dict) else {}
            seconds = float(params.get("seconds", 0) or 0)
            if seconds > 0:
                time.sleep(min(seconds, 30.0))
            return {
                "agent": "agent.worker",
                "method": envelope.method,
                "slept_seconds": seconds,
                "params": envelope.params,
            }

        runtime.register_agent(
            "agent.echo", ["echo.", "debug."], echo_handler, tags=["builtin", "debug"]
        )
        runtime.register_agent(
            "agent.worker",
            ["job.", "sleep.", "task."],
            worker_handler,
            tags=["builtin", "worker"],
        )
        self._builtins_registered = True

    def _register_configured_agents(self) -> None:
        runtime = self._runtime
        runtime_handle = self._resolve_runtime_handle()
        if runtime is None or runtime_handle is None:
            return
        list_registered_agents = getattr(runtime_handle, "list_registered_agents", None)
        if not callable(list_registered_agents):
            return
        try:
            configured_agents = list(list_registered_agents() or [])
        except Exception:  # pragma: no cover - defensive seam
            return

        for raw_agent_id in configured_agents:
            agent_id = str(raw_agent_id or "").strip()
            if not agent_id or agent_id in self._configured_agents_registered:
                continue
            runtime.register_agent(
                agent_id,
                ["delegate", "run", "task", "assist", "chat", "plan", "act"],
                self._configured_agent_handler(agent_id=agent_id),
                tags=["configured", "profile"],
            )
            self._configured_agents_registered.add(agent_id)

    def _configured_agent_handler(
        self, *, agent_id: str
    ) -> Callable[[Any], dict[str, Any]]:
        def _handler(envelope: Any) -> dict[str, Any]:
            runtime_handle = self._resolve_runtime_handle()
            if runtime_handle is None:
                raise RuntimeError("Configured runtime handle unavailable")
            params = envelope.params if isinstance(envelope.params, dict) else {}
            meta = envelope.meta if isinstance(envelope.meta, dict) else {}
            payload = {
                "agent_id": agent_id,
                "message": _delegate_message_from_payload(params),
                "session_id": _delegated_session_id(
                    parent_session_id=str(meta.get("session_id", "") or "").strip(),
                    target_agent_id=agent_id,
                    trace_id=str(getattr(envelope, "trace_id", "") or "").strip(),
                ),
                "channel": "console",
                "target": str(getattr(envelope, "from_agent", "") or "a2a").strip()
                or "a2a",
                "deliver": False,
                "inbound_metadata": _delegated_inbound_metadata(
                    envelope=envelope,
                    target_agent_id=agent_id,
                ),
            }
            target_capability = str(params.get("target_capability", "") or "").strip()
            if target_capability:
                payload["capability_category"] = target_capability
            result = runtime_handle.run_turn(
                payload=payload,
                request_id=str(getattr(envelope, "msg_id", "") or "").strip() or None,
            )
            metadata = result.get("metadata")
            normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            body = str(result.get("body", "") or "").strip()
            response_payload = {
                "summary": body or "Delegated turn completed.",
                "message": body,
                "body": body,
                "agent_id": agent_id,
                "session_id": str(
                    result.get("session_id", "")
                    or normalized_metadata.get("session_id", "")
                ).strip(),
                "run_id": str(
                    result.get("run_id", "") or normalized_metadata.get("run_id", "")
                ).strip(),
                "metadata": normalized_metadata,
            }
            result_summary = _typed_delegation_result_summary(
                normalized_metadata.get("delegation_result_summary")
            )
            if result_summary is not None:
                response_payload["delegation_result_summary"] = result_summary
            return response_payload

        return _handler

    def close(self) -> None:
        runtime = self._runtime
        self._runtime = None
        self._builtins_registered = False
        self._configured_agents_registered.clear()
        if runtime is None:
            return
        closer = getattr(runtime, "close", None)
        if callable(closer):
            try:
                closer(wait=True)
            except TypeError:
                closer()


def _metrics(start_time: float) -> dict[str, Any]:
    return {
        "latency_ms": int((time.monotonic() - start_time) * 1000),
        "tokens_used": 0,
        "cost_estimate": 0.0,
    }


def _normalized_constraints(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip(" -") for line in value.splitlines() if line.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _delegation_context_from_payload(
    params: dict[str, Any],
) -> DelegationContext | None:
    raw = params.get("delegation_context")
    if not isinstance(raw, dict):
        return None
    try:
        return DelegationContext.model_validate(raw)
    except Exception:
        return None


def _delegation_context_block(params: dict[str, Any]) -> str:
    context = _delegation_context_from_payload(params)
    if context is None:
        return ""
    lines = ["[PARENT CONTEXT]"]
    if context.intent_id:
        lines.append(f"intent_id: {context.intent_id}")
    if context.summary:
        lines.append(f"summary: {context.summary}")
    if context.artifacts:
        lines.append("artifacts: " + ", ".join(context.artifacts))
    return "\n".join(lines)


def _sanitized_delegate_summary(*, goal: str, summary: str) -> str:
    normalized_goal = str(goal or "").strip()
    lines: list[str] = []
    for raw_line in str(summary or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("parent goal:"):
            continue
        lines.append(line)
    normalized_summary = "\n".join(lines).strip()
    if normalized_goal and normalized_summary == normalized_goal:
        return ""
    return normalized_summary


def _delegate_message_from_payload(params: dict[str, Any]) -> str:
    goal = str(params.get("goal", "") or "").strip()
    summary = _sanitized_delegate_summary(
        goal=goal,
        summary=str(params.get("summary", "") or "").strip(),
    )
    constraints = _normalized_constraints(params.get("constraints"))
    parts: list[str] = []
    if goal:
        parts.append(goal)
    elif summary:
        parts.append(summary)
    else:
        parts.append("Complete the delegated task.")
    if summary and summary != goal:
        parts.append(f"Context:\n{summary}")
    if constraints:
        parts.append("Constraints:\n" + "\n".join(f"- {item}" for item in constraints))
    parent_context = _delegation_context_block(params)
    if parent_context:
        parts.append(parent_context)
    return "\n\n".join(part for part in parts if part).strip()


def _delegated_session_id(
    *,
    parent_session_id: str,
    target_agent_id: str,
    trace_id: str,
) -> str:
    base = parent_session_id or f"a2a-{trace_id or 'delegated'}"
    return f"{base}::delegate::{target_agent_id}"


def _delegated_inbound_metadata(
    *,
    envelope: Any,
    target_agent_id: str,
) -> dict[str, str]:
    meta = envelope.meta if isinstance(envelope.meta, dict) else {}
    params = envelope.params if isinstance(envelope.params, dict) else {}
    payload = {
        "a2a_parent_session_id": str(meta.get("session_id", "") or "").strip(),
        "a2a_from_agent": str(getattr(envelope, "from_agent", "") or "").strip(),
        "a2a_trace_id": str(getattr(envelope, "trace_id", "") or "").strip(),
        "a2a_delegate_target": str(target_agent_id or "").strip(),
        "a2a_delegated_child": "true",
    }
    parent_context = _delegation_context_from_payload(params)
    if parent_context is not None:
        payload.update(
            {
                "delegation_context_summary": parent_context.summary,
                "delegation_context_artifacts": ",".join(parent_context.artifacts),
                "delegation_context_intent_id": parent_context.intent_id,
            }
        )
    return {key: value for key, value in payload.items() if value}


def _job_record_to_response(job: Any) -> dict[str, Any]:
    task_id = str(getattr(job, "task_id", "") or "").strip()
    raw_state = str(getattr(job, "state", "") or "").strip().upper()
    result_inline = getattr(job, "result_inline", None)
    outputs = dict(result_inline) if isinstance(result_inline, dict) else {}
    error = getattr(job, "error", None)
    normalized_error = dict(error) if isinstance(error, dict) else {}
    summary = str(
        outputs.get("summary")
        or outputs.get("message")
        or normalized_error.get("message")
        or ""
    ).strip()
    if raw_state == A2A_JOB_STATE_PENDING:
        return {
            "status": BRAIN_JOB_STATUS_PENDING,
            "task_id": task_id,
            "poll_after_ms": 1000,
            "summary": summary or "Async A2A job pending.",
        }
    if raw_state == A2A_JOB_STATE_RUNNING:
        return {
            "status": BRAIN_JOB_STATUS_RUNNING,
            "task_id": task_id,
            "poll_after_ms": 1000,
            "summary": summary or "Async A2A job running.",
        }
    if raw_state == A2A_JOB_STATE_SUCCESS:
        return {
            "status": "completed",
            "task_id": task_id,
            "summary": summary or "Async A2A job completed.",
            "outputs": outputs,
        }
    if raw_state == A2A_JOB_STATE_CANCELED:
        return {
            "status": "cancelled",
            "task_id": task_id,
            "summary": summary or "Async A2A job cancelled.",
            "error": normalized_error
            or {"code": "A2A_JOB_CANCELLED", "message": "Job canceled"},
        }
    return {
        "status": "failed",
        "task_id": task_id,
        "summary": summary or "Async A2A job failed.",
        "outputs": outputs,
        "error": normalized_error
        or {"code": "A2A_JOB_FAILED", "message": "Async A2A job failed."},
    }
