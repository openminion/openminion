from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.modules.brain.adapters.a2a import A2actlAdapter
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_RUNNING,
)
from openminion.modules.brain.schemas import ActionError, ActionResult, JobHandle
from openminion.modules.brain.schemas.commands import AgentCommand


@dataclass(frozen=True, slots=True)
class TargetExecutionRecord:
    target_agent_id: str
    from_agent: str
    method: str
    trace_id: str
    params: dict[str, Any]


@dataclass(slots=True)
class RealA2ADelegateHarness:
    home_root: Path
    caller_agent_id: str = "hello-agent"
    adapter: A2actlAdapter = field(init=False)
    records: list[TargetExecutionRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.adapter = A2actlAdapter(
            home_root=self.home_root,
            agent_id=self.caller_agent_id,
        )
        self.adapter._ensure_runtime()

    def close(self) -> None:
        self.adapter.close()

    def register_target(
        self,
        agent_id: str,
        *,
        marker: str,
        capabilities: list[str] | None = None,
    ) -> None:
        normalized_agent_id = str(agent_id).strip()
        normalized_marker = str(marker).strip()

        def _handler(envelope: Any) -> dict[str, Any]:
            params = dict(getattr(envelope, "params", {}) or {})
            record = TargetExecutionRecord(
                target_agent_id=normalized_agent_id,
                from_agent=str(getattr(envelope, "from_agent", "") or ""),
                method=str(getattr(envelope, "method", "") or ""),
                trace_id=str(getattr(envelope, "trace_id", "") or ""),
                params=params,
            )
            self.records.append(record)
            goal = str(params.get("goal", "") or "").strip()
            return {
                "summary": f"{normalized_marker}:target-executed",
                "target_agent_id": normalized_agent_id,
                "target_marker": normalized_marker,
                "received_goal": goal,
                "method": record.method,
                "lineage": {
                    "from_agent": record.from_agent,
                    "target_agent_id": normalized_agent_id,
                    "trace_id": record.trace_id,
                },
            }

        self.adapter.register_agent(
            normalized_agent_id,
            capabilities or ["delegate", "run", "task"],
            _handler,
            tags=["test", "profile-backed"],
        )

    def list_agents(self) -> list[dict[str, Any]]:
        runtime = self.adapter._ensure_runtime()
        return list(runtime.list_agents())

    def trace_events(self, trace_id: str) -> list[dict[str, Any]]:
        runtime = self.adapter._ensure_runtime()
        return list(runtime.query_trace(trace_id))

    def call(
        self,
        *,
        target_agent_id: str,
        goal: str,
        session_id: str,
        trace_id: str,
        expect_async: bool = False,
        method: str = "delegate",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.adapter.call(
            command={
                "target_agent_id": target_agent_id,
                "method": method,
                "params": {"goal": goal},
                "expect_async": expect_async,
                "idempotency_key": idempotency_key
                or f"{session_id}:{trace_id}:{target_agent_id}:{method}:{expect_async}",
            },
            session_id=session_id,
            trace_id=trace_id,
        )

    def action_from_command(
        self,
        *,
        command: AgentCommand,
        session_id: str,
        trace_id: str,
    ) -> tuple[ActionResult, JobHandle | None]:
        raw = self.adapter.call(
            command={
                "target_agent_id": command.target_agent_id,
                "method": command.method,
                "params": dict(command.params),
                "expect_async": bool(command.expect_async),
                "timeout_ms": command.timeout_ms,
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
            },
            session_id=session_id,
            trace_id=trace_id,
        )
        status = str(raw.get("status", "") or "").strip()
        if command.expect_async and status == BRAIN_JOB_STATUS_RUNNING:
            task_id = str(raw.get("task_id", "") or "").strip()
            return (
                ActionResult(
                    command_id=command.command_id,
                    status=BRAIN_ACTION_STATUS_SUCCESS,
                    summary=str(raw.get("summary", "") or "Async A2A job started."),
                ),
                JobHandle(
                    task_id=task_id,
                    command_id=command.command_id,
                    provider="a2actl",
                    status="running",
                    poll_after_ms=int(raw.get("poll_after_ms") or 1000),
                ),
            )
        if status == BRAIN_ACTION_STATUS_SUCCESS:
            return (
                ActionResult(
                    command_id=command.command_id,
                    status=BRAIN_ACTION_STATUS_SUCCESS,
                    summary=str(raw.get("summary", "") or ""),
                    outputs=dict(raw.get("outputs", {}) or {}),
                ),
                None,
            )
        error = dict(raw.get("error", {}) or {})
        message = str(error.get("message") or raw.get("summary") or "A2A failed")
        return (
            ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary=message,
                error=ActionError(
                    code=str(error.get("code") or "A2A_FAILED"),
                    message=message,
                    details=dict(error.get("details") or {}),
                ),
            ),
            None,
        )

    def wait_for_job(
        self,
        task_id: str,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.adapter.poll_task(
                task_id=task_id,
                session_id="harness-poll",
                trace_id="harness-poll",
            )
            if str(last.get("status", "") or "") not in {"running", "pending"}:
                return last
            time.sleep(0.01)
        return last
