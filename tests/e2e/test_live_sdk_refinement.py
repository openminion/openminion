from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

import pytest
from pydantic import BaseModel

from openminion import Agent, tool
from openminion.api import APIRuntime, TurnChunk, TurnResponse

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]

_LIVE_FLAG = "OPENMINION_LIVE_SDK_E2E"


class _LiveReview(BaseModel):
    status: str
    marker: str


@pytest.fixture
def live_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[APIRuntime, str]]:
    if os.getenv(_LIVE_FLAG, "").strip() != "1":
        pytest.skip(f"{_LIVE_FLAG}=1 is not set")

    config_path = Path(os.environ["OPENMINION_LIVE_SDK_CONFIG"])
    if not config_path.is_file():
        pytest.skip(f"missing live SDK config: {config_path}")

    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        yield runtime, os.getenv("OPENMINION_LIVE_SDK_AGENT", "minimax-m2-7")
    finally:
        runtime.close()


def _record(scenario: str, *, execution_id: str) -> None:
    root = Path(os.environ["OPENMINION_LIVE_SDK_ARTIFACT_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "scenario": scenario,
        "status": "passed",
        "execution_id": execution_id,
    }
    (root / f"{scenario}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_live_agent_direct(live_runtime: tuple[APIRuntime, str]) -> None:
    runtime, agent_id = live_runtime
    agent = Agent[str, str](
        runtime=runtime,
        agent_id=agent_id,
        instructions="Reply with the exact marker SDK_DIRECT_OK.",
    )

    result = agent.run("Return the requested marker.", timeout_seconds=300)

    assert "SDK_DIRECT_OK" in result.output
    assert result.id
    _record("agent-direct", execution_id=result.id)


def test_live_agent_decorated_tool(live_runtime: tuple[APIRuntime, str]) -> None:
    runtime, agent_id = live_runtime

    @tool
    def sdk_marker(value: str) -> str:
        """Return a stable SDK test marker."""

        return f"SDK_TOOL_OK:{value}"

    progress: list[object] = []
    agent = Agent[str, str](
        runtime=runtime,
        agent_id=agent_id,
        instructions=(
            "Call sdk_marker once with value live, then report its exact result."
        ),
        tools=[sdk_marker],
        forced_tools=["sdk_marker"],
    )

    result = agent.run_stream(
        "Use the required tool.",
        on_delta=progress.append,
        timeout_seconds=300,
    )

    assert result.output
    assert result.stats["tool_calls"] == 1
    assert result.stats["tool_errors"] == 0
    assert any("sdk_marker" in str(event) for event in progress)
    assert "sdk_marker" not in runtime.tools.list()
    assert result.id
    _record("agent-decorated-tool", execution_id=result.id)


def test_live_agent_structured_output(
    live_runtime: tuple[APIRuntime, str],
) -> None:
    runtime, agent_id = live_runtime
    agent = Agent[str, _LiveReview](
        runtime=runtime,
        agent_id=agent_id,
        instructions=(
            'Return exactly {"status":"passed","marker":"SDK_STRUCTURED_OK"}.'
        ),
        output_type=_LiveReview,
    )

    result = agent.run("Return the requested JSON.", timeout_seconds=300)

    assert result.output == _LiveReview(
        status="passed",
        marker="SDK_STRUCTURED_OK",
    )
    assert result.id
    _record("agent-structured-output", execution_id=result.id)


def test_live_submitted_turn_stream(
    live_runtime: tuple[APIRuntime, str],
) -> None:
    runtime, agent_id = live_runtime
    handle = runtime.submit_turn(
        payload={
            "message": "Reply with the exact marker SDK_SUBMITTED_OK.",
            "session_id": "sdk-live-submitted",
            "agent_id": agent_id,
            "timeout_seconds": 300,
            "deliver": False,
        }
    )

    chunks = list(handle.stream())
    response = handle.result(timeout_s=300)

    assert chunks
    assert all(isinstance(chunk, TurnChunk) for chunk in chunks)
    assert isinstance(response, TurnResponse)
    assert "SDK_SUBMITTED_OK" in response.final_text
    _record(
        "submitted-turn-stream",
        execution_id=handle.trace_id,
    )
