from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool.permission_mode import (
    is_tool_blocked_by_readonly,
)
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.contracts.model_ids import (
    MODEL_AGENT_GET,
    MODEL_AGENT_LIST,
    MODEL_TASK_DELEGATE,
    is_valid_model_tool_id,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_AGENT_GET,
    RUNTIME_AGENT_LIST,
    RUNTIME_TASK_DELEGATE,
    is_valid_runtime_binding_id,
)
from openminion.modules.tool.contracts.display_names import (
    MODEL_TOOL_DISPLAY_NAME_MAP,
    display_name_for_tool_name,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.agent.plugin import (
    AgentGetArgs,
    AgentListArgs,
    TaskDelegateArgs,
    _h_agent_get,
    _h_agent_list,
    _h_task_delegate,
)


def test_canonical_tool_ids_registered() -> None:
    assert is_valid_model_tool_id(MODEL_AGENT_LIST)
    assert is_valid_model_tool_id(MODEL_AGENT_GET)
    assert is_valid_model_tool_id(MODEL_TASK_DELEGATE)
    assert MODEL_AGENT_LIST == "agent.list"
    assert MODEL_AGENT_GET == "agent.get"
    assert MODEL_TASK_DELEGATE == "task.delegate"


def test_canonical_runtime_binding_ids_registered() -> None:
    assert is_valid_runtime_binding_id(RUNTIME_AGENT_LIST)
    assert is_valid_runtime_binding_id(RUNTIME_AGENT_GET)
    assert is_valid_runtime_binding_id(RUNTIME_TASK_DELEGATE)


def test_display_names_populated() -> None:
    assert MODEL_TOOL_DISPLAY_NAME_MAP[MODEL_AGENT_LIST] == "List Agents"
    assert MODEL_TOOL_DISPLAY_NAME_MAP[MODEL_AGENT_GET] == "Get Agent"
    assert MODEL_TOOL_DISPLAY_NAME_MAP[MODEL_TASK_DELEGATE] == "Delegate Task"
    assert display_name_for_tool_name("runtime.agent.list") == "List Agents"


def test_default_registry_includes_agent_family() -> None:
    registry = build_default_tool_registry()
    tools = set(registry.list().keys())
    assert "agent.list" in tools
    assert "agent.get" in tools
    assert "task.delegate" in tools


def test_readonly_blocks_task_delegate() -> None:
    assert is_tool_blocked_by_readonly("task.delegate")
    assert not is_tool_blocked_by_readonly("agent.list")
    assert not is_tool_blocked_by_readonly("agent.get")


def test_agent_list_defaults() -> None:
    args = AgentListArgs()
    assert args.status == ""
    assert args.limit == 50


def test_agent_list_limit_clamped_by_validator() -> None:
    with pytest.raises(Exception):
        AgentListArgs(limit=0)
    with pytest.raises(Exception):
        AgentListArgs(limit=500)


def test_agent_get_requires_agent_id() -> None:
    with pytest.raises(Exception):
        AgentGetArgs(agent_id="")  # type: ignore[call-arg]


def test_task_delegate_requires_fields() -> None:
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="", instruction="hi")  # type: ignore[call-arg]
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="x", instruction="")  # type: ignore[call-arg]


def test_task_delegate_timeout_bounds() -> None:
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="x", instruction="y", timeout_seconds=0)
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="x", instruction="y", timeout_seconds=99_999)


def _ctx_without_storage() -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(raw={}),
        env={},
    )


def _ctx_with_registry(records: list[Any]) -> tuple[SimpleNamespace, dict[str, Any]]:
    calls: dict[str, Any] = {"list_called_with": None, "get_called_with": None}

    class _StubRegistry:
        def list_agents(self, *, status: str | None = None):
            calls["list_called_with"] = status
            return list(records)

        def get_agent(self, agent_id: str):
            calls["get_called_with"] = agent_id
            for r in records:
                if getattr(r, "agent_id", "") == agent_id:
                    return r
            return None

    ctx = SimpleNamespace(
        policy=SimpleNamespace(raw={"storage_path": "/tmp/fake.db"}),
        env={},
        _stub_registry=_StubRegistry(),
    )
    return ctx, calls


def test_agent_list_degrades_when_storage_unconfigured() -> None:
    ctx = _ctx_without_storage()
    out = _h_agent_list({}, ctx)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["agents"] == []
    assert out["count"] == 0
    assert out["storage_unavailable"] is True


def test_agent_get_raises_when_storage_unconfigured() -> None:
    ctx = _ctx_without_storage()
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_agent_get({"agent_id": "alpha"}, ctx)  # type: ignore[arg-type]
    assert exc_info.value.code == "DEPENDENCY_MISSING"
    assert exc_info.value.details["reason_code"] == "agent_registry_unconfigured"


def test_agent_list_returns_records_from_registry(monkeypatch) -> None:
    record = SimpleNamespace(
        agent_id="alpha",
        display_name="Alpha",
        description="primary agent",
        config_path="/p",
        workspace_root="/w",
        tags=["a", "b"],
        status="registered",
        registered_at="2026-05-30",
        updated_at="2026-05-30",
    )
    ctx, calls = _ctx_with_registry([record])

    import openminion.tools.agent.plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_resolve_agent_registry", lambda c: c._stub_registry
    )
    out = _h_agent_list({"status": "registered", "limit": 10}, ctx)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["count"] == 1
    assert out["agents"][0]["agent_id"] == "alpha"
    assert out["agents"][0]["tags"] == ["a", "b"]
    assert calls["list_called_with"] == "registered"


def test_agent_get_returns_record(monkeypatch) -> None:
    record = SimpleNamespace(
        agent_id="alpha",
        display_name="Alpha",
        description="",
        config_path="",
        workspace_root="",
        tags=[],
        status="registered",
        registered_at="",
        updated_at="",
    )
    ctx, calls = _ctx_with_registry([record])
    import openminion.tools.agent.plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_resolve_agent_registry", lambda c: c._stub_registry
    )
    out = _h_agent_get({"agent_id": "alpha"}, ctx)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["agent"]["agent_id"] == "alpha"
    assert calls["get_called_with"] == "alpha"


def test_agent_get_not_found(monkeypatch) -> None:
    ctx, _ = _ctx_with_registry([])
    import openminion.tools.agent.plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_resolve_agent_registry", lambda c: c._stub_registry
    )
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_agent_get({"agent_id": "ghost"}, ctx)  # type: ignore[arg-type]
    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.details["reason_code"] == "agent_not_found"
    assert exc_info.value.details["agent_id"] == "ghost"


def test_task_delegate_seam_unavailable_returns_typed_error() -> None:
    ctx = _ctx_without_storage()  # SimpleNamespace has no a2a_delegate_api
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {
                "agent_id": "beta",
                "instruction": "ship the change",
                "timeout_seconds": 60,
            },
            ctx,  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "DEPENDENCY_MISSING"
    details = exc_info.value.details
    assert details["reason_code"] == "task_delegate_seam_unavailable"
    assert details["agent_id"] == "beta"


def _ctx_with_seam(seam: Any) -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(raw={}), env={}, a2a_delegate_api=seam
    )


def test_task_delegate_happy_path_maps_seam_result() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    calls: dict[str, Any] = {}

    class _Seam:
        def delegate(self, *, agent_id, instruction, timeout_seconds):
            calls.update(
                agent_id=agent_id,
                instruction=instruction,
                timeout_seconds=timeout_seconds,
            )
            return A2ADelegateResult(
                ok=True,
                status="success",
                content="done",
                target_agent_id=agent_id,
                trace_id="t-1",
                task_id="task-1",
                outputs={"k": "v"},
            )

    out = _h_task_delegate(
        {"agent_id": "beta", "instruction": "ship it", "timeout_seconds": 45},
        _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
    )
    assert out["ok"] is True
    assert out["agent_id"] == "beta"
    assert out["status"] == "success"
    assert out["content"] == "done"
    assert out["outputs"] == {"k": "v"}
    assert out["trace_id"] == "t-1"
    assert out["task_id"] == "task-1"
    assert calls == {
        "agent_id": "beta",
        "instruction": "ship it",
        "timeout_seconds": 45,
    }


def test_task_delegate_unknown_target_maps_not_found() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    class _Seam:
        def delegate(self, *, agent_id, instruction, timeout_seconds):
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="AGENT_NOT_FOUND",
                error_message=f"Agent {agent_id!r} is not registered.",
                target_agent_id=agent_id,
            )

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {"agent_id": "ghost", "instruction": "do x"},
            _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "NOT_FOUND"
    details = exc_info.value.details
    assert details["reason_code"] == "task_delegate_failed"
    assert details["delegate_error_code"] == "AGENT_NOT_FOUND"
    assert details["target_agent_id"] == "ghost"


def test_task_delegate_failure_maps_upstream_error() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    class _Seam:
        def delegate(self, *, agent_id, instruction, timeout_seconds):
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="A2A_DELEGATE_FAILED",
                error_message="sub-agent crashed",
                target_agent_id=agent_id,
            )

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {"agent_id": "beta", "instruction": "do x"},
            _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "UPSTREAM_ERROR"
    assert exc_info.value.details["delegate_error_code"] == "A2A_DELEGATE_FAILED"
