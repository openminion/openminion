from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import time
from typing import Any

import pytest

from openminion.base.config import OTELExporterConfig, OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.telemetry.events.module import emit_module_telemetry
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.tool import ToolRegistry
from openminion.modules.tool.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionResult,
)
from openminion.services.agent import AgentService
from openminion.services.runtime.a2a_delegate import A2aRuntimeDelegateAdapter
from openminion.services.runtime.plugins import PluginRegistry
from tests._csc_fixtures import _csc_install_default_agent


class _ScenarioProvider(LLMProvider):
    name = "scenario-provider"

    def __init__(self, tool_names: tuple[str, ...] = ()) -> None:
        self._tool_names = tool_names
        self._calls = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self._calls += 1
        if self._calls == 1 and self._tool_names:
            return ProviderResponse(
                text="",
                model="scenario-model-v1",
                tool_calls=[
                    ProviderToolCall(
                        id=f"scenario-tool-{index}",
                        name=name,
                        arguments={"query": name},
                        source="model",
                    )
                    for index, name in enumerate(self._tool_names, start=1)
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text="Scenario completed.",
            model="scenario-model-v1",
            finish_reason="stop",
        )


class _ScenarioTool(Tool):
    def __init__(self, name: str, *, domain: str, outcome: str) -> None:
        self.name = name
        self.description = f"Execute {name}"
        self.parameters = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        }
        self._domain = domain
        self._outcome = outcome

    def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        emit_module_telemetry(
            context.telemetryctl,
            "emit_canonical_event",
            context.session_id,
            str(context.metadata.get("turn_id") or ""),
            "business.outcome.recorded",
            {
                "domain": self._domain,
                "outcome": self._outcome,
                "status": "completed",
                "value": 1,
                "unit": "operation",
            },
            status="completed",
            logger=logging.getLogger("openminion.tests.e2e.telemetry"),
        )
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            verified=True,
            content=f"completed:{arguments.get('query', '')}",
        )


class _WorkerCall:
    def __init__(self, worker: AgentService) -> None:
        self._worker = worker

    def __call__(self, *, command, session_id, trace_id) -> dict[str, Any]:
        observability = dict(command.get("observability") or {})
        message = Message(
            channel="a2a",
            target="travel-worker",
            body=str(command.get("params", {}).get("instruction") or "plan travel"),
            metadata={
                "session_id": f"{session_id}:worker",
                "request_id": "travel-worker-turn",
                "invocation_id": str(observability.get("invocation_id") or ""),
                "traceparent": str(observability.get("traceparent") or ""),
                "tracestate": str(observability.get("tracestate") or ""),
                "a2a_handoff_id": str(observability.get("handoff_id") or ""),
            },
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = executor.submit(
                asyncio.run, self._worker.run_turn(message)
            ).result()
        return {
            "status": "success",
            "summary": response.text,
            "outputs": {"body": response.text, "trace_id": trace_id},
        }


class _TravelDelegateTool(_ScenarioTool):
    def __init__(self, adapter: A2aRuntimeDelegateAdapter) -> None:
        super().__init__(
            "travel.delegate",
            domain="travel",
            outcome="itinerary.created",
        )
        self._adapter = adapter

    def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        self._adapter.bind_observability(
            session_id=context.session_id,
            turn_id=str(context.metadata.get("turn_id") or ""),
            invocation_id=str(context.metadata.get("invocation_id") or ""),
            execution_id=str(context.metadata.get("execution_id") or ""),
            traceparent=str(context.metadata.get("traceparent") or ""),
            tracestate=str(context.metadata.get("tracestate") or ""),
        )
        result = self._adapter.delegate(
            agent_id="travel-worker",
            instruction=str(arguments.get("query") or "plan travel"),
            timeout_seconds=30,
        )
        if not result.ok:
            return ToolExecutionResult(
                tool_name=self.name,
                ok=False,
                content="",
                error=result.error_message,
            )
        return super().execute(arguments, context)


def _service(
    *,
    agent_name: str,
    provider: LLMProvider,
    telemetryctl: TelemetryCtl,
    tools: tuple[Tool, ...] = (),
) -> AgentService:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, name=agent_name)
    return AgentService(
        config,
        PluginRegistry([]),
        provider,
        logging.getLogger(f"openminion.tests.e2e.{agent_name}"),
        tools=ToolRegistry(list(tools)) if tools else None,
        telemetryctl=telemetryctl,
    )


def _read(path: Path, expected: str) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if expected in text:
                return text
        time.sleep(0.25)
    raise AssertionError(f"{expected!r} was not exported to {path.name}")


def _dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _spans(text: str) -> list[dict[str, Any]]:
    documents = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [
        item
        for document in documents
        for item in _dicts(document)
        if "name" in item and "spanId" in item
    ]


def _span_attribute(span: dict[str, Any], key: str) -> str:
    attribute = next(item for item in span.get("attributes", []) if item["key"] == key)
    return str(next(iter(attribute["value"].values())))


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("scenario", "invocation_id", "tool_names", "outcome"),
    [
        (
            "support",
            "31111111-1111-4111-8111-111111111111",
            ("support.knowledge_base.query",),
            "support.ticket.updated",
        ),
        (
            "coding",
            "32222222-2222-4222-8222-222222222222",
            ("coding.repository.read", "coding.shell.run"),
            "verification.completed",
        ),
    ],
)
def test_real_agent_runtime_scenarios(
    collector_artifacts: Path,
    tmp_path: Path,
    scenario: str,
    invocation_id: str,
    tool_names: tuple[str, ...],
    outcome: str,
) -> None:
    telemetry_service = TelemetryService(
        home_root=tmp_path,
        otel_exporter_config=OTELExporterConfig(
            enabled=True,
            endpoint="http://127.0.0.1:14317",
            protocol="grpc",
            service_name="openminion-agent-runtime-e2e",
        ),
    )
    telemetryctl = TelemetryCtl(telemetry_service)
    tools = tuple(
        _ScenarioTool(name, domain=scenario, outcome=outcome) for name in tool_names
    )
    service = _service(
        agent_name=f"{scenario}-agent",
        provider=_ScenarioProvider(tool_names),
        telemetryctl=telemetryctl,
        tools=tools,
    )
    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target=scenario,
                body=f"run {scenario} workflow",
                metadata={
                    "session_id": f"{scenario}-session",
                    "request_id": f"{scenario}-turn",
                    "invocation_id": invocation_id,
                },
            )
        )
    )
    stored = asyncio.run(telemetry_service.get_invocation_events(invocation_id))
    telemetry_service.close_sync()

    assert response.text == "Scenario completed."
    assert {event.event_type for event in stored}.issuperset(
        {
            "agent.execution.started",
            "agent.execution.completed",
            "llm.call.started",
            "llm.call.completed",
            "tool.execution.started",
            "tool.execution.completed",
            "business.outcome.recorded",
        }
    )
    traces = _read(collector_artifacts / "traces.json", invocation_id)
    logs = _read(collector_artifacts / "logs.json", outcome)
    spans = [span for span in _spans(traces) if invocation_id in json.dumps(span)]
    root = next(
        span for span in spans if span["name"] == f"invoke_agent {scenario}-agent"
    )
    turn = next(span for span in spans if span["name"] == "openminion.turn")
    assert turn["traceId"] == root["traceId"]
    assert turn["parentSpanId"] == root["spanId"]
    assert {f"execute_tool {name}" for name in tool_names}.issubset(
        {span["name"] for span in spans}
    )
    assert outcome in logs


@pytest.mark.e2e
def test_real_multi_agent_travel_runtime(
    collector_artifacts: Path,
    tmp_path: Path,
) -> None:
    invocation_id = "33333333-3333-4333-8333-333333333333"
    telemetry_service = TelemetryService(
        home_root=tmp_path,
        otel_exporter_config=OTELExporterConfig(
            enabled=True,
            endpoint="http://127.0.0.1:14317",
            protocol="grpc",
            service_name="openminion-agent-runtime-e2e",
        ),
    )
    telemetryctl = TelemetryCtl(telemetry_service)
    worker = _service(
        agent_name="travel-worker",
        provider=_ScenarioProvider(),
        telemetryctl=telemetryctl,
    )
    adapter = A2aRuntimeDelegateAdapter(
        a2a_call=_WorkerCall(worker),
        parent_agent_id="travel-coordinator",
        telemetryctl=telemetryctl,
    )
    coordinator = _service(
        agent_name="travel-coordinator",
        provider=_ScenarioProvider(("travel.delegate",)),
        telemetryctl=telemetryctl,
        tools=(_TravelDelegateTool(adapter),),
    )

    response = asyncio.run(
        coordinator.run_turn(
            Message(
                channel="console",
                target="travel",
                body="build an itinerary",
                metadata={
                    "session_id": "travel-session",
                    "request_id": "travel-turn",
                    "invocation_id": invocation_id,
                },
            )
        )
    )
    stored = asyncio.run(telemetry_service.get_invocation_events(invocation_id))
    telemetry_service.close_sync()

    assert response.text == "Scenario completed."
    assert {event.event_type for event in stored}.issuperset(
        {
            "agent.handoff.started",
            "agent.handoff.completed",
            "business.outcome.recorded",
        }
    )
    assert len({event.execution_id for event in stored if event.execution_id}) == 2
    traces = _read(collector_artifacts / "traces.json", invocation_id)
    logs = _read(collector_artifacts / "logs.json", "itinerary.created")
    spans = _spans(traces)
    names = {span["name"] for span in spans}
    assert {
        "invoke_agent travel-coordinator",
        "invoke_agent travel-worker",
        "invoke_agent travel-worker",
        "execute_tool travel.delegate",
    }.issubset(names)
    coordinator_root = next(
        span
        for span in spans
        if span["name"] == "invoke_agent travel-coordinator"
        and _span_attribute(span, "openminion.invocation_id") == invocation_id
        and _span_attribute(span, "openminion.event_type")
        == "agent.execution.completed"
    )
    worker_root = next(
        span
        for span in spans
        if span["name"] == "invoke_agent travel-worker"
        and _span_attribute(span, "openminion.invocation_id") == invocation_id
        and _span_attribute(span, "openminion.event_type")
        == "agent.execution.completed"
    )
    assert worker_root["traceId"] == coordinator_root["traceId"]
    assert worker_root["parentSpanId"]
    assert "itinerary.created" in logs
