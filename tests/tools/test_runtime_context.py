from __future__ import annotations

import json

import pytest

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext, redact_text
from openminion.modules.tool.contracts.schemas import Scope


def make_context(tmp_path, limits: dict[str, int] | None = None) -> RuntimeContext:
    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "workspace"),
            "limits": limits or {},
        }
    )
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    scope: Scope = "WRITE_SAFE"
    return RuntimeContext(
        policy=policy,
        workspace=tmp_path / "workspace",
        run_root=run_root,
        scope=scope,
        confirm=False,
    )


def test_write_artifact_enforces_single_limit(tmp_path):
    ctx = make_context(
        tmp_path, {"max_single_artifact_bytes": 5, "max_artifact_bytes_total": 100}
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        ctx.write_artifact("artifacts/out.txt", b"0123456789", "text/plain")

    assert excinfo.value.code == "POLICY_DENIED"
    assert "max_single_artifact_bytes" in excinfo.value.message


def test_write_artifact_enforces_total_limit(tmp_path):
    ctx = make_context(
        tmp_path, {"max_single_artifact_bytes": 10, "max_artifact_bytes_total": 10}
    )
    ctx.write_artifact("artifacts/a.txt", b"01234", "text/plain")

    with pytest.raises(ToolRuntimeError) as excinfo:
        ctx.write_artifact("artifacts/b.txt", b"012345", "text/plain")

    assert excinfo.value.code == "POLICY_DENIED"
    assert excinfo.value.details["rule"] == "limits.max_artifact_bytes_total"


def test_write_artifact_persists_file_and_metadata(tmp_path):
    ctx = make_context(tmp_path)
    content = b"hello"
    artifact = ctx.write_artifact("artifacts/hello.txt", content, "text/plain")

    written = (ctx.run_root / "artifacts" / "hello.txt").read_bytes()
    assert written == content
    assert artifact.path == "artifacts/hello.txt"
    assert artifact.bytes == len(content)
    assert len(ctx.artifacts) == 1


def test_write_audit_event_appends_jsonl(tmp_path):
    ctx = make_context(tmp_path)
    ctx.write_audit_event({"event": "first"})
    ctx.write_audit_event({"event": "second", "extra": 1})

    audit_file = ctx.run_root / "audit.jsonl"
    lines = audit_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "first"
    assert json.loads(lines[1])["extra"] == 1


def test_write_audit_event_merges_orchestration_metadata(tmp_path):
    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "workspace"),
            "context_metadata": {
                "orchestration": {
                    "mode_name": "act_multi",
                    "workflow_name": "time_lookup",
                    "workflow_kind": "compiled",
                    "command_id": "cmd-123",
                }
            },
        }
    )
    run_root = tmp_path / "run-orchestration"
    run_root.mkdir(parents=True, exist_ok=True)
    ctx = RuntimeContext(
        policy=policy,
        workspace=tmp_path / "workspace",
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )

    ctx.write_audit_event(
        {
            "event": "tool.completed",
            "selected_provider": "mock",
            "selected_backend": "mock-backend",
        }
    )

    row = json.loads((run_root / "audit.jsonl").read_text().strip())
    assert row["event"] == "tool.completed"
    assert row["selected_provider"] == "mock"
    assert row["selected_backend"] == "mock-backend"
    assert row["mode_name"] == "act_multi"
    assert row["workflow_name"] == "time_lookup"
    assert row["workflow_kind"] == "compiled"
    assert row["command_id"] == "cmd-123"


def test_redact_text_modes():
    sample = "API_KEY=abc123 Bearer token SECRET"

    strict = redact_text(sample, "strict")
    normal = redact_text(sample, "normal")
    off = redact_text(sample, "off")

    assert strict.count("[REDACTED]") >= normal.count("[REDACTED]")
    assert off == sample


def test_runtime_context_stores_explicit_env(tmp_path):
    env = EnvironmentConfig.from_sources(
        process_env={"ECC_102_KEY": "process"},
        runtime_env={"ECC_102_KEY": "runtime"},
    )
    policy = Policy(raw={"workspace_root": str(tmp_path / "workspace"), "limits": {}})
    run_root = tmp_path / "run-explicit-env"
    run_root.mkdir(parents=True, exist_ok=True)
    ctx = RuntimeContext(
        policy=policy,
        workspace=tmp_path / "workspace",
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
        env=env,
    )
    assert ctx.env is env
    assert ctx.env.get("ECC_102_KEY") == "process"


def test_runtime_context_default_env_factory_reads_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ECC_102_DEFAULT_ENV_KEY", "present")
    ctx = make_context(tmp_path)
    assert isinstance(ctx.env, EnvironmentConfig)
    assert ctx.env.get("ECC_102_DEFAULT_ENV_KEY") == "present"
