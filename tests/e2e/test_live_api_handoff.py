from __future__ import annotations

import os
from pathlib import Path

import pytest

from openminion.api import Agent, Handoff
from openminion.api.runtime import APIRuntime

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(360)]


def _require_live_api_handoff() -> None:
    enabled = str(os.getenv("OPENMINION_LIVE_API_HANDOFF_E2E", "")).strip()
    if enabled != "1":
        pytest.skip(
            "OPENMINION_LIVE_API_HANDOFF_E2E=1 not set; skipping live API handoff."
        )


def test_live_provider_backed_agent_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the public Agent API hands off to a real configured provider."""
    _require_live_api_handoff()
    framework_root = Path(__file__).resolve().parents[3]
    config_path = framework_root / "test-configs" / "per-agent-minimax-official.json"
    if not config_path.exists():
        pytest.skip(f"missing MiniMax config: {config_path}")

    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_HOME", str(framework_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    runtime = APIRuntime.from_config_path(str(config_path))
    child = Agent(
        runtime=runtime,
        name="provider_child",
        model="MiniMax-M2.7-highspeed",
        instructions="Reply exactly PROVIDER_HANDOFF_OK.",
    )
    parent = Agent(
        runtime=runtime,
        name="provider_parent",
        model="MiniMax-M2.7-highspeed",
        instructions=(
            "The only correct action is to call transfer_to_provider_child exactly "
            "once. Do not answer or ask a question before calling it. Return the "
            "child's response without changing it."
        ),
        handoffs=[Handoff(target=child)],
        forced_tools=["transfer_to_provider_child"],
    )
    progress: list[dict] = []
    try:
        result = parent.run_stream(
            "Call transfer_to_provider_child now with this exact message: "
            "Reply exactly PROVIDER_HANDOFF_OK and nothing else.",
            on_delta=progress.append,
        )
        assert "PROVIDER_HANDOFF_OK" in result.text
        assert "transfer_to_provider_child" not in runtime.tools.list()
        assert any("transfer_to_provider_child" in str(event) for event in progress)
    finally:
        runtime.close()
