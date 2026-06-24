from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from openminion.modules.tool.registry import ToolRegistry
from openminion.services.runtime.routine_context import (
    ToolRegistryPreTurnContext,
)
from openminion.tools.github.interfaces import TOOL_GITHUB_LIST_PRS
from openminion.tools.github.plugin import register as register_github_tools
from openminion.tools.github.providers import (
    provider_registry,
    register_provider,
)


class _RecordingProvider:
    provider_id = "openminion-builtin-github"

    def __init__(self) -> None:
        self.received_ctx: Any = "<not-called>"
        self.received_args: dict[str, Any] | None = None

    def list_prs(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        self.received_ctx = ctx
        self.received_args = dict(args)
        return {"ok": True, "data": {"open_prs": []}}

    def fetch_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {}}

    def fetch_diff(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {}}

    def fetch_comments(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {}}

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {}}

    def healthcheck(self) -> bool:
        return True


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_github_tools(reg)
    return reg


@pytest.fixture
def recording_provider() -> _RecordingProvider:
    provider_registry().reset()
    provider = _RecordingProvider()
    register_provider(provider)
    yield provider
    provider_registry().reset()


def test_canonical_path_constructs_real_runtime_context(
    registry: ToolRegistry, recording_provider: _RecordingProvider
) -> None:
    ctx = ToolRegistryPreTurnContext(
        registry=registry,
        routine_id="job-routine-1",
        session_id="sess-routine-1",
        agent_id="agent-1",
    )
    result = ctx.invoke_tool(
        name=TOOL_GITHUB_LIST_PRS,
        args={"owner": "octocat", "repo": "hello-world"},
    )
    assert result["ok"] is True
    # The recording provider's `received_ctx` MUST be a real RuntimeContext
    # object — proves the canonical path constructed and passed it.
    received = recording_provider.received_ctx
    assert received != "<not-called>"
    assert received is not None
    # RuntimeContext carries policy / scope / agent_id / session_id /
    # tool_name attributes (set by `execute_tool_spec_call`).
    assert hasattr(received, "policy")
    assert hasattr(received, "scope")
    assert hasattr(received, "tool_name")
    # The audit identifiers we plumbed into ToolExecutionContext.metadata
    # propagate into RuntimeContext.agent_id / session_id.
    assert getattr(received, "agent_id", "") == "agent-1"
    assert getattr(received, "session_id", "") == "sess-routine-1"
    assert getattr(received, "tool_name", "") == "github.list_prs"


def test_canonical_path_validates_arguments(
    registry: ToolRegistry, recording_provider: _RecordingProvider
) -> None:
    ctx = ToolRegistryPreTurnContext(
        registry=registry,
        routine_id="job-routine-1",
    )
    # `owner` containing a path separator violates the schema validator.
    result = ctx.invoke_tool(
        name=TOOL_GITHUB_LIST_PRS,
        args={"owner": "evil/path", "repo": "hello-world"},
    )
    assert result["ok"] is False
    # Provider was NOT called — argument validation rejected the call.
    assert recording_provider.received_ctx == "<not-called>"


def test_canonical_path_handles_unregistered_tool_deterministically(
    registry: ToolRegistry,
) -> None:
    ctx = ToolRegistryPreTurnContext(registry=registry)
    result = ctx.invoke_tool(name="github.totally_made_up", args={})
    assert result["ok"] is False
    assert result["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert result["error"]["details"]["reason_code"] == "tool_not_registered"
