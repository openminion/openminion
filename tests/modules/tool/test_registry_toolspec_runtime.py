from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime import dependencies
from openminion.modules.tool.runtime import registry_toolspec
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call


class _EchoEnvTool:
    name = "test.echo_env"
    args_model = dict

    @staticmethod
    def handler(arguments, ctx):
        key = str(arguments.get("key", "")).strip()
        return {
            "ok": True,
            "data": {
                "value": ctx.env.get(key),
            },
        }


class _ConfirmRequiredTool:
    name = "proc.kill"
    args_model = dict

    @staticmethod
    def handler(arguments, ctx):
        del arguments
        ctx.policy.ensure_confirm_if_required(
            tool_name="proc.kill",
            args={},
            confirm=ctx.confirm,
            dangerous_default=False,
        )
        return {"ok": True, "content": "confirmed"}


class _DangerousTool:
    name = "test.dangerous"
    args_model = dict
    dangerous = True

    @staticmethod
    def handler(arguments, ctx):
        del arguments, ctx
        return {"ok": True, "content": "confirmed"}


class _EchoWorkspaceTool:
    name = "test.echo_workspace"
    args_model = dict

    @staticmethod
    def handler(arguments, ctx):
        del arguments
        return {
            "ok": True,
            "data": {
                "workspace_root": ctx.policy.raw.get("workspace_root"),
                "cwd": ctx.policy.raw.get("context_metadata", {}).get("cwd"),
            },
        }


class _EchoTaskOwnerTool:
    name = "test.echo_task_owner"
    args_model = dict

    def __init__(self, expected):
        self.expected = expected

    def handler(self, arguments, ctx):
        del arguments
        return {
            "ok": True,
            "data": {
                "task_manager": ctx.task_manager is self.expected,
                "scheduler": ctx.scheduler_readiness(),
            },
        }


class _HandlerFailureTool:
    name = "test.handler_failure"
    args_model = dict

    @staticmethod
    def handler(_arguments, _ctx):
        raise RuntimeError("handler failure")


class _MissingHandlerTool:
    name = "test.missing_handler"
    args_model = dict
    handler = None


class _ArtifactCtlOwnerTool:
    name = "test.artifactctl_owner"
    args_model = dict

    def __init__(self, expected):
        self.expected = expected

    def handler(self, _arguments, ctx):
        return {
            "ok": True,
            "data": {"same_artifactctl": ctx.artifactctl is self.expected},
        }


def _context(tmp_path, runtime_env):
    return ToolExecutionContext(
        channel="console",
        target="tests",
        session_id="session-ecc-103",
        metadata={
            "workspace_root": str(tmp_path),
            "runtime_env": runtime_env,
        },
    )


def test_tool_execution_context_preserves_confirm_positional_order() -> None:
    names = [field.name for field in fields(ToolExecutionContext)]
    assert names[-2:] == ["confirm", "artifactctl"]


def test_execute_tool_spec_call_uses_runtime_env_from_metadata_mapping(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ECC_103_KEY", raising=False)
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    tool = _EchoEnvTool()
    context = _context(tmp_path, {"ECC_103_KEY": "runtime"})

    result = execute_tool_spec_call(
        tool=tool,
        arguments={"key": "ECC_103_KEY"},
        context=context,
    )

    assert result.ok is True
    assert result.data["value"] == "runtime"


def test_execute_tool_spec_call_normalizes_working_dir_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    working_dir = tmp_path / "focus-wd"
    working_dir.mkdir()

    result = execute_tool_spec_call(
        tool=_EchoWorkspaceTool(),
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="session-working-dir",
            metadata={"working_dir": str(working_dir)},
        ),
    )

    assert result.ok is True
    expected = str(Path(working_dir).resolve())
    assert result.data["workspace_root"] == expected
    assert result.data["cwd"] == str(working_dir)


def test_execute_tool_spec_call_preserves_task_runtime_owners(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    task_manager = object()

    def scheduler_readiness():
        return {"state": "ready"}

    result = execute_tool_spec_call(
        tool=_EchoTaskOwnerTool(task_manager),
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="session-task-owner",
            metadata={"workspace_root": str(tmp_path)},
            task_manager=task_manager,
            scheduler_readiness=scheduler_readiness,
        ),
    )

    assert result.ok is True
    assert result.data == {"task_manager": True, "scheduler": {"state": "ready"}}


def test_execute_tool_spec_call_runtime_env_allows_json_metadata_payload(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ECC_103_KEY_JSON", raising=False)
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    tool = _EchoEnvTool()
    context = _context(
        tmp_path,
        json.dumps({"ECC_103_KEY_JSON": "runtime-json"}),
    )

    result = execute_tool_spec_call(
        tool=tool,
        arguments={"key": "ECC_103_KEY_JSON"},
        context=context,
    )

    assert result.ok is True
    assert result.data["value"] == "runtime-json"


def test_execute_tool_spec_call_keeps_process_env_precedence_over_runtime_env(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ECC_103_KEY_PRECEDENCE", "process")
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    tool = _EchoEnvTool()
    context = _context(tmp_path, {"ECC_103_KEY_PRECEDENCE": "runtime"})

    result = execute_tool_spec_call(
        tool=tool,
        arguments={"key": "ECC_103_KEY_PRECEDENCE"},
        context=context,
    )

    assert result.ok is True
    assert result.data["value"] == "process"


def test_execute_tool_spec_call_requires_confirm_without_replay_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    result = execute_tool_spec_call(
        tool=_ConfirmRequiredTool(),
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="session-confirm",
            metadata={"workspace_root": str(tmp_path)},
        ),
    )

    assert result.ok is False
    assert result.data["error_code"] == "CONFIRM_REQUIRED"


def test_execute_tool_spec_call_enforces_dangerous_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    denied = execute_tool_spec_call(
        tool=_DangerousTool(),
        arguments={},
        context=ToolExecutionContext(
            channel="api",
            target="tests",
            session_id="session-dangerous",
            metadata={"workspace_root": str(tmp_path)},
        ),
    )
    confirmed = execute_tool_spec_call(
        tool=_DangerousTool(),
        arguments={},
        context=ToolExecutionContext(
            channel="api",
            target="tests",
            session_id="session-dangerous",
            metadata={"workspace_root": str(tmp_path)},
            confirm=True,
        ),
    )

    assert denied.ok is False
    assert denied.data["error_code"] == "CONFIRM_REQUIRED"
    assert confirmed.ok is True


def test_execute_tool_spec_call_honors_policy_replay_confirmation_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    result = execute_tool_spec_call(
        tool=_ConfirmRequiredTool(),
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="session-confirm",
            metadata={
                "workspace_root": str(tmp_path),
                "confirmation_source": "policy_replay",
                "confirmation_grant_id": "local-confirmation-test",
            },
        ),
    )

    assert result.ok is True
    assert result.content == "confirmed"


def test_execute_tool_spec_call_honors_explicit_context_confirmation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    result = execute_tool_spec_call(
        tool=_ConfirmRequiredTool(),
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="session-confirm",
            metadata={"workspace_root": str(tmp_path)},
            confirm=True,
        ),
    )

    assert result.ok is True
    assert result.content == "confirmed"


def test_execute_tool_spec_call_closes_owned_artifactctl(tmp_path, monkeypatch):
    owned = MagicMock()
    monkeypatch.setattr(registry_toolspec, "resolve_artifactctl", lambda: owned)

    result = execute_tool_spec_call(
        tool=_EchoEnvTool(),
        arguments={"key": "missing"},
        context=_context(tmp_path, {}),
    )

    assert result.ok is True
    owned.close.assert_called_once_with()


@pytest.mark.parametrize("tool", [_HandlerFailureTool(), _MissingHandlerTool()])
def test_execute_tool_spec_call_closes_owned_artifactctl_on_tool_failure(
    tmp_path,
    monkeypatch,
    tool,
):
    owned = MagicMock()
    monkeypatch.setattr(registry_toolspec, "resolve_artifactctl", lambda: owned)

    result = execute_tool_spec_call(
        tool=tool,
        arguments={},
        context=_context(tmp_path, {}),
    )

    assert result.ok is False
    owned.close.assert_called_once_with()


def test_execute_tool_spec_call_closes_owned_artifactctl_on_dependency_failure(
    tmp_path,
    monkeypatch,
):
    owned = MagicMock()
    monkeypatch.setattr(registry_toolspec, "resolve_artifactctl", lambda: owned)

    def fail_dependency(*_args, **_kwargs):
        raise RuntimeError("dependency failure")

    monkeypatch.setattr(
        dependencies,
        "enforce_tool_dependencies",
        fail_dependency,
    )

    result = execute_tool_spec_call(
        tool=_EchoEnvTool(),
        arguments={"key": "missing"},
        context=_context(tmp_path, {}),
    )

    assert result.ok is False
    assert "dependency failure" in result.error
    owned.close.assert_called_once_with()


def test_execute_tool_spec_call_closes_owned_artifactctl_on_context_failure(
    tmp_path,
    monkeypatch,
):
    owned = MagicMock()
    monkeypatch.setattr(registry_toolspec, "resolve_artifactctl", lambda: owned)

    def fail_context(**_kwargs):
        raise RuntimeError("context failure")

    monkeypatch.setattr(
        registry_toolspec,
        "_build_tool_runtime_context",
        fail_context,
    )

    with pytest.raises(RuntimeError, match="context failure"):
        execute_tool_spec_call(
            tool=_EchoEnvTool(),
            arguments={"key": "missing"},
            context=_context(tmp_path, {}),
        )

    owned.close.assert_called_once_with()


def test_execute_tool_spec_call_does_not_close_borrowed_artifactctl(
    tmp_path,
    monkeypatch,
):
    borrowed = MagicMock()

    def unexpected_resolve():
        raise AssertionError("borrowed controller should be reused")

    monkeypatch.setattr(
        registry_toolspec,
        "resolve_artifactctl",
        unexpected_resolve,
    )
    context = _context(tmp_path, {})
    context.artifactctl = borrowed

    result = execute_tool_spec_call(
        tool=_ArtifactCtlOwnerTool(borrowed),
        arguments={},
        context=context,
    )

    assert result.ok is True
    assert result.data["same_artifactctl"] is True
    borrowed.close.assert_not_called()


def test_execute_tool_spec_call_propagates_artifactctl_construction_failure(
    tmp_path,
    monkeypatch,
):
    def fail_resolve():
        raise RuntimeError("artifact startup failure")

    monkeypatch.setattr(registry_toolspec, "resolve_artifactctl", fail_resolve)

    with pytest.raises(RuntimeError, match="artifact startup failure"):
        execute_tool_spec_call(
            tool=_EchoEnvTool(),
            arguments={"key": "missing"},
            context=_context(tmp_path, {}),
        )
