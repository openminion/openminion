from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.tool.base import ToolExecutionContext
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
    name = "file.delete"
    args_model = dict

    @staticmethod
    def handler(arguments, ctx):
        del arguments
        ctx.policy.ensure_confirm_if_required(
            tool_name="file.delete",
            args={},
            confirm=ctx.confirm,
            dangerous_default=False,
        )
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
