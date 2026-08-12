from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.services.agent import AgentService
from openminion.services.runtime.plugins import PluginRegistry
from tests._csc_fixtures import _csc_install_default_agent


class _SuccessProvider(LLMProvider):
    name = "success"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text=f"ok:{request.user_message}", model="model-1")


class _FailureProvider(LLMProvider):
    name = "failure"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        raise RuntimeError("provider failed")


class _CancelledProvider(LLMProvider):
    name = "cancelled"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        raise asyncio.CancelledError


def _service(tmp_path: Path, provider: LLMProvider):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)
    telemetry = TelemetryService(home_root=tmp_path)
    agent = AgentService(
        config,
        PluginRegistry([]),
        provider,
        logging.getLogger("openminion.tests.invocation-terminal"),
        telemetryctl=TelemetryCtl(telemetry),
    )
    return agent, telemetry


def _message(*, durable: bool = False) -> Message:
    metadata = {"session_id": "session-1", "request_id": "turn-1"}
    if durable:
        metadata["invocation_id"] = "durable-invocation"
    return Message(
        channel="console",
        target="local-user",
        body="hello",
        metadata=metadata,
    )


def _events(telemetry: TelemetryService):
    return asyncio.run(telemetry.get_events())


def test_runtime_scoped_success_projects_exact_execution_terminal(
    tmp_path: Path,
) -> None:
    agent, telemetry = _service(tmp_path, _SuccessProvider())
    try:
        asyncio.run(agent.run_turn(_message()))
        events = _events(telemetry)
    finally:
        telemetry.close_sync()

    invocation = [
        event for event in events if event.event_type.startswith("agent.invocation.")
    ]
    execution_terminal = next(
        event for event in events if event.event_type == "agent.execution.completed"
    )
    assert [event.event_type for event in invocation] == [
        "agent.invocation.started",
        "agent.invocation.completed",
    ]
    assert invocation[-1].timestamp == execution_terminal.timestamp
    assert invocation[-1].data["source_event_id"] == execution_terminal.event_id
    assert invocation[-1].execution_id == execution_terminal.execution_id


@pytest.mark.parametrize(
    ("provider", "execution_type", "invocation_type", "error_type"),
    [
        (
            _FailureProvider(),
            "agent.execution.failed",
            "agent.invocation.failed",
            RuntimeError,
        ),
        (
            _CancelledProvider(),
            "agent.execution.cancelled",
            "agent.invocation.cancelled",
            asyncio.CancelledError,
        ),
    ],
)
def test_runtime_failure_and_cancellation_project_same_source(
    tmp_path: Path,
    provider: LLMProvider,
    execution_type: str,
    invocation_type: str,
    error_type: type[BaseException],
) -> None:
    agent, telemetry = _service(tmp_path, provider)
    try:
        with pytest.raises(error_type):
            asyncio.run(agent.run_turn(_message()))
        events = _events(telemetry)
    finally:
        telemetry.close_sync()

    execution = next(event for event in events if event.event_type == execution_type)
    invocation = next(event for event in events if event.event_type == invocation_type)
    assert invocation.timestamp == execution.timestamp
    assert invocation.data["source_event_id"] == execution.event_id


def test_durable_execution_completion_does_not_close_invocation(
    tmp_path: Path,
) -> None:
    agent, telemetry = _service(tmp_path, _SuccessProvider())
    try:
        asyncio.run(agent.run_turn(_message(durable=True)))
        events = _events(telemetry)
    finally:
        telemetry.close_sync()

    assert any(event.event_type == "agent.execution.completed" for event in events)
    assert not any(event.event_type.startswith("agent.invocation.") for event in events)
