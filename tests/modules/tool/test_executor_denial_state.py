from __future__ import annotations

import asyncio

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.policy.runtime.security import (
    SecurityPolicyEngine,
    ToolBudgetPolicy,
    ToolBudgetState,
)
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.trace.metadata import apply_content_policy
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.tool.executor import record_external_tool_results
from openminion.services.agent.execution.policy import (
    build_policy_adapter,
    filter_allowed_tool_calls,
)


@dataclass
class _Telemetry:
    events: list[TelemetryEvent] = field(default_factory=list)

    async def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict,
        *,
        trace_id: str = "",
        status: str | None = None,
        error: dict | None = None,
    ) -> None:
        data = dict(payload)
        data.update({"trace_id": trace_id, "status": status})
        if error:
            data["error"] = dict(error)
        self.events.append(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type=event_type,
                data=data,
            )
        )

    async def emit_module_counter(self, *args, **kwargs) -> None:
        return None


class _ToolProfiles:
    def __init__(self, profile: Any) -> None:
        self._profile = profile

    def policy_for(self, tool_name: str) -> Any:
        del tool_name
        return self._profile

    def sidecar_for(self, tool_name: str) -> str:
        del tool_name
        return ""


def _context(telemetry: _Telemetry) -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="test",
        target="local",
        session_id="session-1",
        metadata={"turn_id": "turn-1", "trace_id": "trace-1"},
        telemetryctl=telemetry,
    )


def _service_port(policy: SecurityPolicyEngine, profile: Any) -> Any:
    return SimpleNamespace(
        tools=_ToolProfiles(profile),
        security_policy=policy,
        identity_agent_id="agent-test",
        identity_security_lab_facts={},
    )


def _runtime() -> Any:
    return SimpleNamespace(
        inbound=SimpleNamespace(channel="test", target="local", metadata={}),
        approval_callback=None,
    )


async def _produced_denial_results(
    *,
    profile: Any,
    budget_policy: ToolBudgetPolicy | None = None,
    budget_state: ToolBudgetState | None = None,
) -> tuple[list[ProviderToolCall], list[ToolExecutionResult]]:
    """Run the real policy/budget producer path and return its denied results."""

    policy = SecurityPolicyEngine(tool_budget_policy=budget_policy)
    service_port = _service_port(policy, profile)
    runtime = _runtime()
    adapter = build_policy_adapter(
        service_port,
        runtime,
        tool_budget_state=budget_state,
        turn_boundary_adapter=None,
    )
    calls = [ProviderToolCall(id="call-denied", name="vault.read", arguments={})]
    allowed, _events, denied, _overrides = await filter_allowed_tool_calls(
        service_port,
        runtime,
        calls,
        policy_adapter=adapter,
    )
    assert allowed == []
    assert len(denied) == 1
    return calls, denied


async def _record(
    telemetry: _Telemetry,
    calls: list[ProviderToolCall],
    results: list[ToolExecutionResult],
) -> ToolExecutionResult:
    record_external_tool_results(
        context=_context(telemetry), calls=calls, results=results
    )
    # Emissions are scheduled on the running loop by the shared emitter.
    await asyncio.sleep(0)
    return results[0]


def _terminal(telemetry: _Telemetry) -> dict:
    return telemetry.events[-1].data


@pytest.mark.asyncio
async def test_policy_denial_producer_records_denied_state() -> None:
    telemetry = _Telemetry()
    calls, results = await _produced_denial_results(
        profile=SimpleNamespace(
            required_scopes_all=("secrets.read",), risk="low", budget_cost=1
        )
    )
    result = await _record(telemetry, calls, results)

    assert result.data["denial_source"] == "policy"
    assert result.data["blocked_kind"] == "policy_denied"
    assert result.state == "denied"
    assert [event.event_type for event in telemetry.events] == [
        "tool.execution.started",
        "tool.execution.failed",
    ]
    assert _terminal(telemetry)["error"] == {
        "type": "missing_tool_scope",
        "code": "missing_tool_scope",
        "category": "policy",
    }


@pytest.mark.asyncio
async def test_budget_denial_producer_records_denied_state() -> None:
    telemetry = _Telemetry()
    calls, results = await _produced_denial_results(
        profile=SimpleNamespace(required_scopes_all=(), risk="low", budget_cost=6),
        budget_policy=ToolBudgetPolicy(
            max_calls_per_run=10,
            max_calls_per_tool=10,
            max_budget_cost_per_run=10,
        ),
        budget_state=ToolBudgetState(budget_cost_total=5),
    )
    assert results[0].data["error_code"] == "tool_budget_cost_exceeded"
    assert results[0].data["denial_source"] == "budget"
    result = await _record(telemetry, calls, results)

    assert result.state == "denied"
    assert _terminal(telemetry)["error"]["type"] == "tool_budget_cost_exceeded"
    assert _terminal(telemetry)["error"]["category"] == "policy"


@pytest.mark.asyncio
async def test_ordinary_failures_stay_error() -> None:
    telemetry = _Telemetry()
    calls = [
        ProviderToolCall(id="call-not-found", name="missing", arguments={}),
        ProviderToolCall(id="call-timeout", name="slow", arguments={}),
    ]
    results = [
        ToolExecutionResult(
            tool_name="missing",
            ok=False,
            content="",
            error="not found",
            call_id="call-not-found",
            data={"error_code": "NOT_FOUND"},
        ),
        ToolExecutionResult(
            tool_name="slow",
            ok=False,
            content="",
            error="timed out",
            call_id="call-timeout",
            data={"error_code": "TIMEOUT"},
        ),
    ]
    record_external_tool_results(
        context=_context(telemetry), calls=calls, results=results
    )
    await asyncio.sleep(0)

    assert [item.state for item in results] == ["error", "error"]
    failed = [
        event
        for event in telemetry.events
        if event.event_type == "tool.execution.failed"
    ]
    assert [event.data["error"]["category"] for event in failed] == [
        "execution",
        "timeout",
    ]


@pytest.mark.asyncio
async def test_budget_reason_code_without_explicit_facts_stays_error() -> None:
    telemetry = _Telemetry()
    calls = [ProviderToolCall(id="call-budget-ish", name="untrusted", arguments={})]
    results = [
        ToolExecutionResult(
            tool_name="untrusted",
            ok=False,
            content="",
            error="tool_budget_cost_exceeded",
            call_id="call-budget-ish",
            data={"reason_code": "tool_budget_cost_exceeded"},
        )
    ]
    result = await _record(telemetry, calls, results)

    assert result.state == "error"


@pytest.mark.asyncio
async def test_success_stays_success() -> None:
    telemetry = _Telemetry()
    calls = [ProviderToolCall(id="call-ok", name="lookup", arguments={})]
    results = [
        ToolExecutionResult(
            tool_name="lookup",
            ok=True,
            content="result",
            verified=True,
            call_id="call-ok",
            data={"exit_code": 0},
        )
    ]
    result = await _record(telemetry, calls, results)

    assert result.state == "ok"
    assert [event.event_type for event in telemetry.events] == [
        "tool.execution.started",
        "tool.execution.completed",
    ]
    assert "error" not in _terminal(telemetry)


@pytest.mark.asyncio
async def test_policy_reason_code_branch_without_facts_still_denies() -> None:
    telemetry = _Telemetry()
    calls = [ProviderToolCall(id="call-policy-code", name="legacy", arguments={})]
    results = [
        ToolExecutionResult(
            tool_name="legacy",
            ok=False,
            content="",
            error="security_deny",
            call_id="call-policy-code",
            data={"reason_code": "POLICY_LEGACY_DENIED"},
        )
    ]
    result = await _record(telemetry, calls, results)

    assert result.state == "denied"


@pytest.mark.asyncio
async def test_denial_telemetry_row_keeps_error_dict_and_redaction_policy() -> None:
    telemetry = _Telemetry()
    calls, results = await _produced_denial_results(
        profile=SimpleNamespace(
            required_scopes_all=("secrets.read",), risk="low", budget_cost=1
        )
    )
    await _record(telemetry, calls, results)
    terminal = _terminal(telemetry)

    assert not {"denial_source", "blocked_kind"} & set(terminal)
    assert "error_details" not in terminal
    assert set(terminal["error"]) == {"type", "code", "category"}

    sanitized = apply_content_policy(terminal, allow_sensitive_content=False)
    assert sanitized["error"] == terminal["error"]
    assert sanitized["_telemetry_policy"]["sensitive_content"] == "omitted"


@pytest.mark.parametrize(
    "facts",
    [
        {"denial_source": []},
        {"blocked_kind": {}},
        {"denial_source": ["policy"], "blocked_kind": {"approval_required"}},
    ],
    ids=["empty-list-source", "empty-dict-kind", "truthy-non-string-both"],
)
@pytest.mark.asyncio
async def test_non_string_denial_facts_stay_error(facts: dict[str, Any]) -> None:
    """A non-string fact is not one of its field's defined denial values."""

    telemetry = _Telemetry()
    calls = [ProviderToolCall(id="call-not-found", name="missing", arguments={})]
    results = [
        ToolExecutionResult(
            tool_name="missing",
            ok=False,
            content="",
            error="not found",
            call_id="call-not-found",
            data={"error_code": "NOT_FOUND", **facts},
        )
    ]

    result = await _record(telemetry, calls, results)

    assert result.state == "error"
    assert _terminal(telemetry)["error"]["category"] == "execution"
