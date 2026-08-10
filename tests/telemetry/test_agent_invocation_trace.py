from __future__ import annotations

import asyncio
import logging

import pytest

from tests._csc_fixtures import _csc_install_default_agent
from tests.services.agent._agent_service_support import (
    AgentService,
    FakeProvider,
    Message,
    OpenMinionConfig,
    PluginRegistry,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.services.agent.execution import flow


def _service() -> AgentService:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)
    return AgentService(
        config,
        PluginRegistry([]),
        FakeProvider(),
        logging.getLogger("openminion.tests.invocation"),
    )


def test_durable_invocation_is_preserved_and_execution_is_finite() -> None:
    response = asyncio.run(
        _service().run_turn(
            Message(
                channel="console",
                target="me",
                body="hello",
                metadata={"invocation_id": "invocation-1"},
            )
        )
    )
    assert response.metadata["invocation_id"] == "invocation-1"
    assert response.metadata["execution_id"]
    assert response.metadata["invocation_scope"] == "durable"


def test_non_durable_call_gets_runtime_scoped_invocation() -> None:
    response = asyncio.run(
        _service().run_turn(Message(channel="console", target="me", body="hello"))
    )
    assert response.metadata["invocation_id"]
    assert response.metadata["execution_id"]
    assert response.metadata["invocation_scope"] == "runtime"


def test_agent_runtime_emits_finite_execution_turn_and_phase_events(tmp_path) -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)
    telemetry_service = TelemetryService(home_root=tmp_path)
    service = AgentService(
        config,
        PluginRegistry([]),
        FakeProvider(),
        logging.getLogger("openminion.tests.invocation.telemetry"),
        telemetryctl=TelemetryCtl(telemetry_service),
    )

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hello",
                metadata={
                    "session_id": "session-1",
                    "request_id": "turn-1",
                    "invocation_id": "11111111-1111-4111-8111-111111111111",
                },
            )
        )
    )
    events = asyncio.run(
        telemetry_service.get_invocation_events(response.metadata["invocation_id"])
    )
    telemetry_service.close_sync()

    assert [event.event_type for event in events] == [
        "agent.execution.started",
        "agent.turn.started",
        "agent.phase.started",
        "llm.call.started",
        "llm.call.completed",
        "agent.phase.completed",
        "agent.turn.completed",
        "agent.execution.completed",
    ]
    assert {event.execution_id for event in events} == {
        response.metadata["execution_id"]
    }


def test_agent_runtime_closes_lifecycle_when_planning_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)
    telemetry_service = TelemetryService(home_root=tmp_path)
    service = AgentService(
        config,
        PluginRegistry([]),
        FakeProvider(),
        logging.getLogger("openminion.tests.invocation.failure"),
        telemetryctl=TelemetryCtl(telemetry_service),
    )
    invocation_id = "22222222-2222-4222-8222-222222222222"

    def fail_planning(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("planning failed")

    monkeypatch.setattr(flow, "_build_and_apply_tool_plan", fail_planning)
    with pytest.raises(RuntimeError, match="planning failed"):
        asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="hello",
                    metadata={
                        "session_id": "session-2",
                        "request_id": "turn-2",
                        "invocation_id": invocation_id,
                    },
                )
            )
        )

    events = asyncio.run(telemetry_service.get_invocation_events(invocation_id))
    telemetry_service.close_sync()

    assert [event.event_type for event in events] == [
        "agent.execution.started",
        "agent.turn.started",
        "agent.phase.started",
        "agent.phase.failed",
        "agent.turn.failed",
        "agent.execution.failed",
    ]
