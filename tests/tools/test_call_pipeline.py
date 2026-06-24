from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from openminion.modules.tool.adapters import AllowAllSafetyAdapter, LocalPolicyAdapter
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolSpec
from openminion.modules.tool.runtime import (
    make_error_envelope,
    make_ok_envelope,
    new_run_id,
)
from openminion.modules.tool.contracts.schemas import (
    Artifact,
    CmdRunArgs,
    LogEntry,
    Scope,
    WorkspaceInfo,
)


def _sample_artifact() -> Artifact:
    return Artifact(
        type="file",
        path="artifacts/output.txt",
        mime="text/plain",
        bytes=5,
        sha256="abc123",
    )


def _sample_log(level: str = "info", msg: str = "ok") -> LogEntry:
    return LogEntry(
        ts=datetime.now(timezone.utc).isoformat(), level=level, msg=msg, meta={}
    )


def test_make_ok_envelope_populates_fields(tmp_path):
    run_id = new_run_id()
    started = datetime.now(timezone.utc).isoformat()
    scope: Scope = "WRITE_SAFE"
    artifacts = [_sample_artifact()]
    logs = [_sample_log()]

    envelope = make_ok_envelope(
        tool="cmd.run",
        run_id=run_id,
        request_id="req-123",
        scope=scope,
        started_at=started,
        workspace=tmp_path,
        artifacts=artifacts,
        logs=logs,
        data={"status": "done"},
    )

    assert envelope.ok is True
    assert envelope.tool == "cmd.run"
    assert envelope.run_id == run_id
    assert envelope.workspace.root == str(tmp_path)
    assert envelope.duration_ms >= 0
    assert envelope.data["status"] == "done"
    assert len(envelope.artifacts) == 1
    assert len(envelope.logs) == 1


def test_make_error_envelope_sets_error_payload(tmp_path):
    run_id = new_run_id()
    started = datetime.now(timezone.utc).isoformat()
    scope: Scope = "READ_ONLY"
    error = ToolRuntimeError("POLICY_DENIED", "boom", {"rule": "x"})

    envelope = make_error_envelope(
        tool="cmd.run",
        run_id=run_id,
        request_id=None,
        scope=scope,
        started_at=started,
        workspace=tmp_path,
        artifacts=[],
        logs=[_sample_log("error", "failed")],
        error=error,
    )

    assert envelope.ok is False
    assert envelope.error is not None
    assert envelope.error.code == "POLICY_DENIED"
    assert envelope.error.details["rule"] == "x"
    assert envelope.duration_ms >= 0
    assert envelope.workspace == WorkspaceInfo(root=str(tmp_path), relative_root=".")


def _policy(raw: dict[str, object], tmp_path: Path) -> Policy:
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    payload = {
        "workspace_root": workspace,
        "paths": {
            "read_allow": [workspace],
            "write_allow": [workspace],
        },
        "commands": {"mode": "allowlist", "allow": ["echo"]},
    }
    payload.update(raw)
    return Policy(raw=payload)


def _tool_spec() -> ToolSpec:
    return ToolSpec("cmd.run", CmdRunArgs, "WRITE_SAFE", handler=lambda args, ctx: {})


def test_local_policy_adapter_allows_valid_command(tmp_path):
    policy = _policy({"commands": {"mode": "allowlist", "allow": ["echo"]}}, tmp_path)
    adapter = LocalPolicyAdapter(
        policy=policy,
        workspace=tmp_path,
        scope="WRITE_SAFE",
        confirm=True,
    )
    spec = _tool_spec()

    decision = adapter.evaluate(
        tool_name="cmd.run",
        tool_spec=spec,
        args={"argv": ["echo", "ok"], "cwd": str(tmp_path / "workspace")},
    )

    assert decision.allowed is True
    assert decision.reason


def test_local_policy_adapter_denies_blocked_command(tmp_path):
    policy = _policy({"commands": {"mode": "allowlist", "allow": ["git"]}}, tmp_path)
    adapter = LocalPolicyAdapter(
        policy=policy,
        workspace=tmp_path,
        scope="WRITE_SAFE",
        confirm=True,
    )
    spec = _tool_spec()

    decision = adapter.evaluate(
        tool_name="cmd.run",
        tool_spec=spec,
        args={"argv": ["echo", "nope"], "cwd": str(tmp_path / "workspace")},
    )

    assert decision.allowed is False
    assert decision.code == "POLICY_DENIED"
    assert decision.details.get("rule") == "commands.allow"


def test_allow_all_safety_adapter_passes_through():
    adapter = AllowAllSafetyAdapter()
    decision = adapter.evaluate(tool="file.read_file", args={"path": "."})

    assert decision.allowed is True
    assert decision.reason == "Safety checks passed"
