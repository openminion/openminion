from __future__ import annotations

import json

from typer.testing import CliRunner

from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.runtime.credentials import resolve_credential_ref
from openminion.tools.ops import cli
from openminion.tools.ops.api import operator_state, target_inspect
from openminion.tools.ops.cli import app
from openminion.tools.ops.contracts import (
    OperationRequest,
    OperationTarget,
    OpsConfig,
    TransportFacts,
    TransportResult,
)
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
        "openminion.tools.ops.service.importlib.util.find_spec",
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


def test_target_inspect_probe_is_explicit_and_ssh_only(monkeypatch) -> None:
    target = OpsConfig.model_validate(
        {
            "targets": [
                {
                    "target_id": "staging",
                    "kind": "ssh",
                    "address": "staging.example",
                    "username": "operator",
                    "credential_ref": resolve_credential_ref(
                        "probe",
                        scope_kind="tool_family",
                        scope_id="ops",
                        env_name="OPENMINION_OPS_PROBE",
                    ),
                    "endpoint_trust": {"host_key": "ssh-ed25519 fixture"},
                }
            ]
        }
    ).targets[0]

    class ProbeTransport:
        calls = 0

        def connect(self, candidate):
            self.calls += 1
            return TransportFacts(
                kind="ssh",
                platform=candidate.platform,
                connected=True,
                capabilities=("command",),
            )

        def close(self):
            return None

    transport = ProbeTransport()
    service = OpsService(
        targets=TargetRegistry((target,)),
        transports={"ssh": transport},
    )
    monkeypatch.setattr(
        "openminion.tools.ops.service.importlib.util.find_spec",
        lambda _name: object(),
    )

    metadata = target_inspect(service, "staging")
    probed = target_inspect(service, "staging", probe=True)

    assert transport.calls == 1
    assert metadata["data"]["probe"]["status"] == "not_requested"
    assert probed["data"]["dependency_available"] is True
    assert probed["data"]["probe"]["status"] == "succeeded"
    assert probed["data"]["probe"]["target_revision"] == target.revision
    assert probed["data"]["probe"]["checked_at"]

    local = local_ops_service().inspect_target_readiness("local", probe=True)
    assert local["probe"]["status"] == "unsupported"
    assert local["probe"]["reason_code"] == "unsupported_transport"


def test_cli_plan_and_confirmed_run_share_service(monkeypatch) -> None:
    service = local_ops_service()
    service.action_policy = PolicyCtl.with_sqlite(
        ":memory:", config=PolicyConfig(mode="enforce")
    )
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)
    runner = CliRunner()

    planned = runner.invoke(
        app,
        [
            "command-plan",
            "local",
            "printf",
            "ready",
            "--session",
            "session-1",
        ],
    )
    plan = json.loads(planned.stdout)
    denied = runner.invoke(
        app,
        [
            "command-run",
            plan["plan_id"],
            plan["plan_hash"],
            "--session",
            "session-1",
        ],
    )
    completed = runner.invoke(
        app,
        [
            "command-run",
            plan["plan_id"],
            plan["plan_hash"],
            "--session",
            "session-1",
            "--confirm",
        ],
    )

    assert planned.exit_code == 0
    assert denied.exit_code != 0
    assert completed.exit_code == 0
    assert json.loads(completed.stdout)["status"] == "succeeded"


def test_cli_stream_writes_chunks_only_to_stderr(monkeypatch) -> None:
    class StreamingTransport:
        def run(self, _target, argv, *, output_sink=None, **_kwargs):
            if output_sink is not None:
                output_sink("stdout", "early\n")
            return TransportResult(argv=argv, return_code=0, stdout="early\nfinal\n")

        def cancel(self, _operation_id):
            return False

        def close(self):
            return None

    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="local", kind="local"),)),
        transports={"local": StreamingTransport()},
        action_policy=PolicyCtl.with_sqlite(
            ":memory:", config=PolicyConfig(mode="enforce")
        ),
    )
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)
    plan = service.plan_command(
        target_id="local", argv=("printf", "ready"), session_id="session-1"
    )

    result = CliRunner().invoke(
        app,
        [
            "command-run",
            plan.plan_id,
            plan.plan_hash,
            "--session",
            "session-1",
            "--confirm",
            "--stream",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "succeeded"
    assert result.stderr == "early\n"


def test_cli_default_command_run_keeps_output_in_final_json(monkeypatch) -> None:
    class BufferedTransport:
        def run(self, _target, argv, *, output_sink=None, **_kwargs):
            assert output_sink is None
            return TransportResult(argv=argv, return_code=0, stdout="ready")

        def cancel(self, _operation_id):
            return False

        def close(self):
            return None

    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="local", kind="local"),)),
        transports={"local": BufferedTransport()},
        action_policy=PolicyCtl.with_sqlite(
            ":memory:", config=PolicyConfig(mode="enforce")
        ),
    )
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)
    plan = service.plan_command(
        target_id="local", argv=("printf", "ready"), session_id="session-1"
    )

    result = CliRunner().invoke(
        app,
        [
            "command-run",
            plan.plan_id,
            plan.plan_hash,
            "--session",
            "session-1",
            "--confirm",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "succeeded"
    assert result.stderr == ""


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


def test_cli_job_mark_interrupted_requires_confirmation_and_reason(monkeypatch) -> None:
    service = local_ops_service()
    job = service.jobs.submit(
        OperationRequest(
            operation_id="observe-1",
            target_id="local",
            profile_id="host.snapshot",
            session_id="session-1",
        ),
        target_revision=1,
    )
    service.jobs.update(job.job_id, status="running")
    monkeypatch.setattr(cli, "_configured_service", lambda _config: service)
    runner = CliRunner()

    missing_confirm = runner.invoke(
        app,
        ["job-mark-interrupted", job.job_id, "--reason", "checked"],
    )
    missing_reason = runner.invoke(
        app,
        ["job-mark-interrupted", job.job_id, "--confirm"],
    )
    marked = runner.invoke(
        app,
        [
            "job-mark-interrupted",
            job.job_id,
            "--confirm",
            "--reason",
            "remote state checked",
        ],
    )

    assert missing_confirm.exit_code != 0
    assert missing_reason.exit_code != 0
    assert marked.exit_code == 0
    assert json.loads(marked.stdout)["status"] == "failed"
