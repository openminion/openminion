from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_FAILED,
    BRAIN_JOB_STATUS_RUNNING,
)
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas.base import new_uuid


class LocalA2AAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}

    def call(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]:
        target = str(command.get("target_agent_id", "unknown"))
        method = str(command.get("method", "unknown"))
        params = (
            command.get("params", {}) if isinstance(command.get("params"), dict) else {}
        )
        if command.get("expect_async"):
            task_id = new_uuid()
            self._jobs[task_id] = {
                "status": BRAIN_JOB_STATUS_RUNNING,
                "task_id": task_id,
                "poll_after_ms": 1000,
                "summary": "Async A2A job started.",
                "outputs": {
                    "target_agent_id": target,
                    "method": method,
                    "params": params,
                    "session_id": session_id,
                    "trace_id": trace_id,
                },
            }
            return {
                "status": BRAIN_JOB_STATUS_RUNNING,
                "task_id": task_id,
                "poll_after_ms": 1000,
                "summary": "Async A2A job started.",
            }
        return {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": f"A2A call completed: {target}.{method}",
            "outputs": {
                "target_agent_id": target,
                "method": method,
                "params": params,
                "session_id": session_id,
                "trace_id": trace_id,
            },
            "artifact_refs": [],
            "memory_refs": [],
            "metrics": {"latency_ms": 1, "tokens_used": 0, "cost_estimate": 0.0},
        }

    @staticmethod
    def _missing_job_result(task_id: str) -> dict[str, Any]:
        return {
            "status": BRAIN_JOB_STATUS_FAILED,
            "summary": f"Unknown async A2A job: {task_id}",
            "error": {
                "code": "A2A_JOB_NOT_FOUND",
                "message": f"Unknown async A2A job: {task_id}",
            },
        }

    def poll_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]:
        del session_id, trace_id
        existing = self._jobs.get(str(task_id or "").strip())
        if existing is None:
            return self._missing_job_result(task_id)
        return dict(existing)

    def cancel_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]:
        del session_id, trace_id
        normalized = str(task_id or "").strip()
        existing = self._jobs.get(normalized)
        if existing is None:
            return self._missing_job_result(task_id)
        if existing.get("status") not in {"cancelled", "completed", "failed"}:
            existing = {
                **existing,
                "status": "cancelled",
                "summary": "Async A2A job cancelled.",
                "error": {
                    "code": "A2A_JOB_CANCELLED",
                    "message": "Async A2A job cancelled.",
                },
            }
            self._jobs[normalized] = existing
        return dict(existing)
