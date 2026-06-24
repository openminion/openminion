from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.api.runtime import APIRuntime
from openminion.base.config import (
    OpenMinionConfig,
    RunProfileOverrides,
    UnknownProfileError,
)
from openminion.api.core.turn_execution import open_turn_submission
from openminion.api.turns import run_turn


def test_api_turn_adapter_uses_api_runtime_facade() -> None:
    runtime = mock.Mock()
    runtime.run_turn.return_value = {"id": "turn-1"}

    result = run_turn(
        config_path=None,
        payload={"message": "hello", "session_id": "session-1"},
        runtime=runtime,
        request_id="req-1",
    )

    runtime.run_turn.assert_called_once_with(
        payload={"message": "hello", "session_id": "session-1"},
        request_id="req-1",
        progress_callback=None,
        # TESS-04-approval: API now forwards approval_callback too.
        approval_callback=None,
    )
    assert result == {"id": "turn-1"}


def test_open_turn_submission_uses_api_runtime_submit_turn() -> None:
    runtime_handle = SimpleNamespace(
        request=SimpleNamespace(session_id="session-1", trace_id="trace-1"),
        timeout_s=12.0,
        trace_id="trace-1",
        result=lambda timeout_s=None: None,
        stream=lambda timeout_s=None: iter(()),
    )
    runtime = mock.Mock()
    runtime.submit_turn.return_value = runtime_handle

    submission = open_turn_submission(
        config_path=None,
        runtime=runtime,
        body={"message": "hello", "session_id": "session-1"},
    )

    runtime.submit_turn.assert_called_once_with(
        payload={"message": "hello", "session_id": "session-1"},
    )
    assert submission.request is runtime_handle.request
    assert submission.handle is runtime_handle
    assert submission.timeout_s == 12.0


def test_api_runtime_resolve_agent_profile_fails_closed_for_unknown_profile() -> None:
    runtime = object.__new__(APIRuntime)
    runtime.config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"}
            },
            "default_agent": "hello-agent",
        }
    )
    runtime.run_profile_overrides = RunProfileOverrides()

    with pytest.raises(UnknownProfileError):
        APIRuntime.resolve_agent_profile(runtime, "missing-profile")


def test_api_runtime_resolve_agent_profile_combines_runtime_and_call_overrides() -> (
    None
):
    runtime = object.__new__(APIRuntime)
    runtime.config = OpenMinionConfig.from_dict(
        {
            "agents": {"hello-agent": {"name": "hello-agent", "provider": "openai"}},
            "default_agent": "hello-agent",
        }
    )
    runtime.run_profile_overrides = RunProfileOverrides(
        provider="anthropic",
        system_prompt="Global override prompt.",
    )

    profile = APIRuntime.resolve_agent_profile(
        runtime,
        "hello-agent",
        overrides=RunProfileOverrides(system_prompt="Call override prompt."),
    )

    assert profile.name == "hello-agent"
    assert profile.provider == "anthropic"
    assert profile.system_prompt == "Call override prompt."
