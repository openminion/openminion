from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.executor import TurnExecutor
from openminion.services.agent.execution.loop_quality import observe_tool_calls
from tests._csc_fixtures import _csc_install_default_agent


def _call(command: str, *, call_id: str = "") -> ProviderToolCall:
    return ProviderToolCall(
        name="exec.run",
        arguments={"command": command},
        id=call_id,
        source="test",
    )


def test_loop_quality_observes_exact_duplicate_call_shapes() -> None:
    observations = observe_tool_calls(
        [
            _call("command -v nasm", call_id="one"),
            _call("command -v nasm", call_id="two"),
        ],
        seen_signatures={},
    )

    assert len(observations) == 1
    assert observations[0]["event_kind"] == "duplicate_tool_call_observed"
    assert observations[0]["action_class"] == "discovery"
    assert observations[0]["batch_count"] == "2"


def test_loop_quality_does_not_flag_changed_arguments() -> None:
    observations = observe_tool_calls(
        [
            _call("command -v nasm", call_id="one"),
            _call("command -v clang", call_id="two"),
        ],
        seen_signatures={},
    )

    assert observations == []


def test_loop_quality_observes_redundant_discovery_version_across_batches() -> None:
    seen: dict[str, int] = {}

    assert observe_tool_calls([_call("nasm --version")], seen_signatures=seen) == []
    observations = observe_tool_calls([_call("nasm --version")], seen_signatures=seen)

    assert len(observations) == 1
    assert observations[0]["event_kind"] == "redundant_discovery_version_observed"
    assert observations[0]["action_class"] == "version"
    assert observations[0]["turn_count"] == "2"


def test_loop_quality_changed_denial_retry_shape_is_not_duplicate() -> None:
    seen: dict[str, int] = {}

    first = observe_tool_calls(
        [_call("which nasm && nasm --version")],
        seen_signatures=seen,
    )
    retry = observe_tool_calls([_call("command -v nasm")], seen_signatures=seen)

    assert first == []
    assert retry == []


def test_executor_observes_without_suppressing_legitimate_calls() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    events: list[dict[str, object]] = []
    executed: list[ProviderToolCall] = []

    class _Tools:
        def execute_calls(self, calls, context):
            del context
            executed.extend(list(calls))
            return ToolExecutionBatch(
                results=[
                    ToolExecutionResult(
                        tool_name=str(getattr(call, "name", "") or ""),
                        ok=True,
                        verified=True,
                        content="ok",
                    )
                    for call in calls
                ]
            )

    inbound = Message(channel="console", target="user", body="hello", metadata={})
    runtime = SimpleNamespace(
        inbound=inbound,
        progress_callback=events.append,
        tool_call_signature_counts={},
        tool_loop_observations=[],
    )
    service = SimpleNamespace(
        _config=config,
        _identity_agent_id="agent-1",
        _tool_selection=None,
        _tools=_Tools(),
        _security_policy=None,
        _self_improvement=None,
        _logger=None,
        _home_root=None,
    )
    executor = TurnExecutor(service=service, runtime=runtime)

    batch, security_events, denied = asyncio.run(
        executor.execute_tool_calls(
            [
                _call("command -v nasm", call_id="one"),
                _call("command -v nasm", call_id="two"),
            ],
            tool_budget_state=None,
        )
    )

    loop_events = [
        event for event in events if event.get("kind") == "tool_loop_observation"
    ]
    assert len(loop_events) == 1
    assert loop_events[0]["event_kind"] == "duplicate_tool_call_observed"
    assert len(executed) == 2
    assert len(batch.results) == 2
    assert security_events == []
    assert denied is False
