from __future__ import annotations

import json

from typer.testing import CliRunner

from openminion.modules.runtime.credentials import resolve_credential_ref
from openminion.tools.ops import cli
from openminion.tools.ops.api import operator_state
from openminion.tools.ops.cli import app
from openminion.tools.ops.contracts import OpsConfig
from openminion.tools.ops.registry import TargetRegistry
from openminion.tools.ops.service import OpsService, local_ops_service


def test_operator_state_is_redacted_and_renderer_neutral() -> None:
    state = operator_state(local_ops_service())

    assert state["ok"] is True
    assert set(state["data"]) == {
        "tool_family",
        "targets",
        "jobs",
        "plans",
        "evidence",
        "pending_approvals",
        "disabled_reasons",
    }
    assert state["data"]["tool_family"]["id"] == "ops"
    assert state["data"]["tool_family"]["guidance"] == "ops.safety.v1"
    target = state["data"]["targets"][0]
    assert "credential_ref" not in target
    assert "endpoint_trust" not in target


def test_cli_status_matches_shared_api_envelope(monkeypatch) -> None:
    service = local_ops_service()
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    expected = json.loads(json.dumps(operator_state(service)))
    assert json.loads(result.stdout) == expected


def test_operator_state_reports_protocol_scoped_missing_extras(monkeypatch) -> None:
    credential = resolve_credential_ref(
        "ops-test",
        scope_kind="tool_family",
        scope_id="ops",
        env_name="OPENMINION_OPS_TEST",
    )
    targets = OpsConfig.model_validate(
        {
            "targets": [
                {
                    "target_id": "windows",
                    "kind": "winrm",
                    "address": "windows.example",
                    "username": "operator",
                    "credential_ref": credential,
                    "ca_trust_path": "/etc/ssl/certs/ops.pem",
                },
                {
                    "target_id": "pod",
                    "kind": "kubernetes",
                    "credential_ref": credential,
                    "context": "staging",
                    "namespace": "agents",
                    "pod": "worker-0",
                },
                {
                    "target_id": "node",
                    "kind": "ssm",
                    "credential_ref": credential,
                    "account_id": "123456789012",
                    "region": "us-west-2",
                    "managed_node_id": "mi-123",
                    "document_name": "AWS-RunShellScript",
                },
            ]
        }
    ).targets
    monkeypatch.setattr(
        "openminion.tools.ops.api.importlib.util.find_spec",
        lambda _name: None,
    )

    state = operator_state(OpsService(targets=TargetRegistry(targets), transports={}))[
        "data"
    ]

    assert state["disabled_reasons"] == {
        "windows": "install the 'remote-winrm' extra",
        "pod": "install the 'remote-kubernetes' extra",
        "node": "install the 'remote-aws' extra",
    }
    assert all(target["transport_ready"] is False for target in state["targets"])


def test_cli_plan_and_confirmed_run_share_service(monkeypatch) -> None:
    service = local_ops_service()
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)
    runner = CliRunner()

    planned = runner.invoke(app, ["command-plan", "local", "printf", "ready"])
    plan = json.loads(planned.stdout)
    denied = runner.invoke(
        app,
        ["command-run", plan["plan_id"], plan["plan_hash"]],
    )
    completed = runner.invoke(
        app,
        ["command-run", plan["plan_id"], plan["plan_hash"], "--confirm"],
    )

    assert planned.exit_code == 0
    assert denied.exit_code != 0
    assert completed.exit_code == 0
    assert json.loads(completed.stdout)["status"] == "succeeded"


def test_cli_file_read_uses_shared_service(monkeypatch, tmp_path) -> None:
    source = tmp_path / "status.txt"
    source.write_text("ready", encoding="utf-8")
    service = local_ops_service()
    local = service.inspect_target("local")
    service.targets.register(
        local.model_copy(update={"workspace_scopes": (str(tmp_path),), "revision": 2})
    )
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)

    result = CliRunner().invoke(
        app,
        ["file-read", "local", str(source), "--max-bytes", "5"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["stdout_preview"] == "ready"
    assert payload["operation_id"].startswith("file-read:")
    assert str(source) not in payload["operation_id"]
