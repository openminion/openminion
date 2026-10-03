from __future__ import annotations

import json
import time
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.api.core.exposure import (
    RuntimeToolExposureMixin,
    _security_lab_activation,
)
from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.tools import handle_request as tools_handle_request
from openminion.base.config import OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.base.runtime.sandbox import ExecResult
from openminion.modules.runtime.sandboxes.security_lab import (
    SecurityLabConfig,
    SecurityLabExecutionDetails,
    SecurityLabExecutionFacts,
    coerce_security_lab_config,
)
from openminion.base.types import Message
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.runtime.sandboxes.docker import DockerLabScope
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage import InMemoryIdentityStore
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.exposure import ToolExposureService
from openminion.modules.tool.exposure.service import (
    SECURITY_LAB_ALLOWED_TOOL_IDS,
    resolve_security_lab_metadata,
)
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.exec.handlers import _h_exec_run
from openminion.tools.security.family import SECURITY_FAMILY
from openminion.tools.security.plugin import _h_publish_report
from openminion.tools.security.schemas import SecurityLabPublishArgs
from openminion.services.agent.context.runtime import build_context
from openminion.services.runtime.ingress.execution import _enforce_security_lab_route
from openminion.services.runtime.ingress.requests import (
    _security_lab_inbound_metadata,
)
from openminion.services.runtime.ingress.types import TurnRequestError
from tests.artifact.utils import make_config

_DIGEST = "example.invalid/worker@sha256:" + "a" * 64
_AGENT = "security-researcher-local-lab"
_ROOT = Path(__file__).resolve().parents[3]


def _active_report_payload(evidence_refs: list[str]) -> dict[str, object]:
    return {
        "activity_class": "local_lab_active",
        "objective": "Validate the synthetic authorization boundary.",
        "validation_evidence": evidence_refs,
        "findings": [],
        "summary": "No candidate findings were submitted.",
        "limitations": "Synthetic target only.",
    }


def _lab_ref(index: int) -> str:
    return f"artifact://sha256/{index:064x}"


def _lab_finding(evidence_ref: str, finding_id: str = "LAB-1") -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "disposition": "candidate",
        "title": "Synthetic route returned data",
        "category": "authorization",
        "severity": "medium",
        "confidence": "high",
        "explanation": "The approved request reached the observable route.",
        "evidence_ref": evidence_ref,
        "location": {
            "component": "synthetic-target",
            "path": "/defect",
            "method": "GET",
        },
    }


def _config() -> SecurityLabConfig:
    return SecurityLabConfig(
        daemon_socket="unix:///var/run/docker.sock",
        target_container="security-target",
        worker_image=_DIGEST,
        executable_allowlist=("curl",),
        worker_uid=10001,
        worker_gid=10001,
        agent_identity_id=_AGENT,
        command_timeout_seconds=30,
        max_output_bytes=4096,
        cpu_limit=0.5,
        memory_bytes=67_108_864,
        pids_limit=32,
        label="Local lab",
    )


def _scope() -> DockerLabScope:
    return DockerLabScope(
        daemon_id="daemon-local",
        target_container_id="target-id",
        target_image_id="target-image",
        worker_image_id="worker-image",
        worker_image_digest=_DIGEST,
        config_fingerprint="c" * 64,
        resolved_scope_fingerprint="r" * 64,
    )


def _identity(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "agent_id": _AGENT,
        "profile_revision": 1,
        "profile_version": "profile-v1",
        "tool_use": "restricted",
        "allowed_tools": ("respond", "clarify", "exec.run", "security.publish_report"),
        "lab_required": True,
    }
    values.update(overrides)
    return values


def _exposure_service() -> ToolExposureService:
    profile = next(
        profile
        for profile in SECURITY_FAMILY.exposure_profiles
        if profile.profile_id == "security_lab"
    )
    return ToolExposureService([profile])


def test_local_lab_examples_are_explicit_and_exact() -> None:
    profile_path = _ROOT / "examples/security-researcher-local-lab/profile.json"
    identity_path = _ROOT / "examples/identity/security-researcher-local-lab.yaml"
    config = OpenMinionConfig.from_dict(json.loads(profile_path.read_text()))
    assert config.default_agent == "default-agent"
    lab_config = coerce_security_lab_config(config.runtime.security_lab)
    assert lab_config is not None
    assert lab_config.agent_identity_id == _AGENT

    identity = IdentityCtl(store=InMemoryIdentityStore())
    try:
        assert identity.load_profiles_from_path(identity_path) == [_AGENT]
        loaded = identity.get_profile(_AGENT)
    finally:
        identity.close()
    assert loaded is not None
    assert loaded.tool_posture.tool_use == "restricted"
    assert set(loaded.tool_posture.allowed_tools) == {
        "respond",
        "clarify",
        "exec.run",
        "security.publish_report",
    }


def test_lab_context_removes_host_and_project_facts(tmp_path: Path) -> None:
    config = OpenMinionConfig()
    config.runtime.security_lab = _config()
    metadata = _security_lab_inbound_metadata(
        runtime=SimpleNamespace(config=config),
        agent_id=_AGENT,
        inbound_metadata={
            "workspace_root": "/host/source",
            "cwd": "/host/source",
            "openminion_ephemeral_workspace_roots": "/host/tmp",
            "project_context_body": "host project secret",
        },
    )
    assert metadata == {
        "lab_required": "true",
        "cwd": "/workspace",
        "workspace_root": "/workspace",
    }

    service = SimpleNamespace(
        _config=config,
        _home_root=tmp_path,
        workspace_root="/host/source",
        _tools=None,
        _identity_security_lab_facts=lambda: {"lab_required": True},
        _inject_identity_system_prompt=lambda **values: values["system_prompt"],
    )
    result = build_context(
        service=service,
        inbound=Message(
            channel="cli",
            target="focus",
            body="inspect",
            metadata={
                "workspace_root": "/host/source",
                "project_context_body": "host project secret",
            },
        ),
        history=[],
    )
    assert "workspace_root: /workspace" in result.system_prompt
    assert "/host/source" not in result.system_prompt
    assert "host project secret" not in result.system_prompt


def test_lab_route_requires_exact_non_room_session() -> None:
    config = OpenMinionConfig()
    config.runtime.security_lab = _config()
    session = SimpleNamespace(
        session_key=f"agent:{_AGENT}|channel:cli|target:tui",
        owner_agent_id=_AGENT,
    )
    runtime = SimpleNamespace(
        config=config,
        sessions=SimpleNamespace(
            get_session=lambda _session_id: session,
            list_participants=lambda _session_id: [],
        ),
    )
    request = SimpleNamespace(profile_agent_id=_AGENT, session_id="session-1")
    _enforce_security_lab_route(
        runtime=runtime,
        request=request,
        routed_agents=(_AGENT,),
        routing_mode="addressed",
    )
    with pytest.raises(TurnRequestError, match="exact-agent non-room"):
        _enforce_security_lab_route(
            runtime=runtime,
            request=request,
            routed_agents=(_AGENT, "other"),
            routing_mode="broadcast",
        )


def test_activation_freezes_identity_scope_and_refuses_duplicates() -> None:
    config = _config()
    scope = _scope()
    runner = SimpleNamespace(config=config, preflight=lambda: scope)
    service = _exposure_service()
    session = SimpleNamespace(
        session_key=f"agent:{_AGENT}|channel:cli|target:tui",
        owner_agent_id=_AGENT,
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(runtime=SimpleNamespace(security_lab=config)),
        security_lab_runner=runner,
        sessions=SimpleNamespace(get_session=lambda _session_id: session),
        resolve_agent_service=lambda _agent_id: SimpleNamespace(
            _identity_security_lab_facts=lambda: _identity()
        ),
        tools=SimpleNamespace(exposure_service=service),
    )

    activation = _security_lab_activation(
        runtime,
        session_id="session-1",
        target_id="security-target",
        approved=True,
        ttl_seconds=60,
        activation_reason="approved synthetic target",
        approved_by="operator",
        policy_source="focus",
    )
    metadata = resolve_security_lab_metadata(
        service,
        config=config,
        runner=runner,
        identity=_identity(),
        session_id="session-1",
    )

    assert activation.agent_id == _AGENT
    assert metadata["security_lab_state"] == "ready"
    assert metadata["target_container_id"] == "target-id"
    status = RuntimeToolExposureMixin._security_lab_status(
        runtime, session_id="session-1"
    )
    assert status["daemon_state"] == "ready"
    assert status["allowed_tools"] == sorted(SECURITY_LAB_ALLOWED_TOOL_IDS)
    assert status["limits"]["max_output_bytes"] == config.max_output_bytes
    assert status["approved_by"] == "operator"
    with pytest.raises(ToolRuntimeError, match="already has an active approval"):
        _security_lab_activation(
            runtime,
            session_id="session-1",
            target_id="security-target",
            approved=True,
            ttl_seconds=60,
            activation_reason="duplicate",
            approved_by="operator",
            policy_source="focus",
        )
    service.deactivate(
        "security_lab",
        session_id="session-1",
        target_id="security-target",
    )
    with pytest.raises(ToolRuntimeError, match="finite and greater than zero"):
        _security_lab_activation(
            runtime,
            session_id="session-1",
            target_id="security-target",
            approved=True,
            ttl_seconds=float("nan"),
            activation_reason="invalid ttl",
            approved_by="operator",
            policy_source="focus",
        )
    activation = _security_lab_activation(
        runtime,
        session_id="session-1",
        target_id="security-target",
        approved=True,
        ttl_seconds=60,
        activation_reason="approved synthetic target",
        approved_by="operator",
        policy_source="focus",
    )
    assert activation.agent_id == _AGENT
    assert (
        resolve_security_lab_metadata(
            service,
            config=config,
            runner=runner,
            identity=_identity(profile_revision=2),
            session_id="session-1",
        )["security_lab_state"]
        == "unavailable"
    )
    assert (
        resolve_security_lab_metadata(
            service,
            config=config,
            runner=runner,
            identity=_identity(tool_use="all"),
            session_id="session-1",
        )["security_lab_reason"]
        == "security_lab_identity_posture_changed"
    )


def test_activation_route_returns_typed_denial() -> None:
    def deny_activation(*_args: object, **_kwargs: object) -> None:
        raise ToolRuntimeError("INVALID_ARGUMENT", "approval required")

    runtime = SimpleNamespace(
        runtime_manager=object(),
        activate_tool_profile=deny_activation,
    )
    result = tools_handle_request(
        APIRouteContext(None, runtime, None, None, "request-1"),
        method_name="POST",
        path="/v1/tools/exposure/activate",
        body={"profile_id": "security_lab", "session_id": "session-1"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "tool_exposure_activation_denied"


class _LabRunner:
    def __init__(self) -> None:
        self.config = _config()
        self.calls: list[tuple[object, object]] = []
        self.stdout_bytes = b"ok\x00\xff\x1b]8;;https://bad.invalid\x07link"
        self.output_limited = False

    def run_exec(self, spec, sandbox) -> ExecResult:
        self.calls.append((spec, sandbox))
        return ExecResult(
            returncode=0,
            stdout="replacement must not own evidence bytes",
            stderr="",
            execution_details=SecurityLabExecutionDetails(
                stdout_bytes=self.stdout_bytes,
                stderr_bytes=b"",
                stdout_observed_bytes=32,
                stderr_observed_bytes=0,
                stdout_truncated=self.output_limited,
                output_limited=self.output_limited,
                terminated=self.output_limited,
                limit_reason="output" if self.output_limited else None,
                execution_facts=SecurityLabExecutionFacts(
                    runner="docker-security-lab",
                    worker_container_id="worker-1",
                    worker_image_digest=_DIGEST,
                    daemon_id="daemon-local",
                    target_container_id="target-id",
                    target_image_id="target-image",
                    isolation_mode="target-network-namespace",
                    started_at="2026-10-03T00:00:00+00:00",
                    duration_ms=12,
                    executable_basename="curl",
                    argument_count=2,
                    canonical_argv_sha256="f" * 64,
                    timeout_seconds=10,
                    max_output_bytes=4096,
                    cpu_limit=0.5,
                    memory_bytes=67_108_864,
                    pids_limit=32,
                ),
            ),
        )


@pytest.fixture
def lab_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "cas"
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    artifactctl = ArtifactCtl(make_config(data_root))
    runner = _LabRunner()
    ordinary_runner = SimpleNamespace(calls=[])
    metadata = {
        "agent_id": _AGENT,
        "lab_required": "true",
        "activity_class": "local_lab_active",
        "security_lab_state": "ready",
        "activation_id": "activation-1",
        "activation_expires_at": str(time.time() + 300),
        "profile_revision": "1",
        "profile_version": "profile-v1",
        "config_fingerprint": "c" * 64,
        "resolved_scope_fingerprint": "s" * 64,
        "daemon_id": "daemon-local",
        "target_container_id": "target-id",
        "target_image_id": "target-image",
        "worker_image_digest": _DIGEST,
        "isolation_mode": "target-network-namespace",
        "security_lab_target": "security-target",
    }
    run_root = tmp_path / "run"
    run_root.mkdir()
    ctx = RuntimeContext(
        policy=Policy(
            raw={"workspace_root": str(tmp_path), "context_metadata": metadata}
        ),
        workspace=tmp_path,
        run_root=run_root,
        scope="POWER_USER",
        confirm=True,
        env=EnvironmentConfig(values={}),
        artifactctl=artifactctl,
        permission_mode="ask",
        session_id="session-1",
        agent_id=_AGENT,
        tool_name="exec.run",
        sandbox_runner=ordinary_runner,
        security_lab_runner=runner,
        security_lab_metadata=lambda: dict(metadata),
    )
    try:
        yield ctx, runner, ordinary_runner
    finally:
        artifactctl.close()


def test_lab_exec_uses_direct_argv_and_persists_exact_bytes(lab_context) -> None:
    ctx, runner, ordinary_runner = lab_context
    result = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/defect",
            "host": "sandbox",
            "security": "deny",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    spec, sandbox = runner.calls[0]
    assert spec.cmd == ["curl", "http://127.0.0.1:8080/defect"]
    assert spec.cwd == "/workspace"
    assert sandbox.security_lab.activation_id == "activation-1"
    assert ordinary_runner.calls == []
    assert result["evidence_artifact"]["ref"].startswith("artifact://sha256/")
    stdout_ref = result["stdout_artifact"]["ref"]
    assert (
        ctx.artifactctl.read_bytes(stdout_ref)
        == b"ok\x00\xff\x1b]8;;https://bad.invalid\x07link"
    )
    assert "\\x1b" in result["stdout_preview"]
    evidence = json.loads(
        ctx.artifactctl.read_bytes(result["evidence_artifact"]["ref"])
    )
    assert evidence["schema"] == "exec-evidence/v1"
    assert "http://127.0.0.1" not in json.dumps(evidence)
    assert evidence["result"]["stdout"]["retained_bytes"] == 33


def test_lab_exec_preserves_empty_raw_output(lab_context) -> None:
    ctx, runner, _ordinary_runner = lab_context
    runner.stdout_bytes = b""
    result = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/empty",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    assert result["stdout_artifact"] is None
    assert result["stdout_preview"] is None
    assert result["metrics"]["bytes_out"] == 0


def test_lab_exec_keeps_untrusted_marker_after_preview_truncation(lab_context) -> None:
    ctx, runner, _ordinary_runner = lab_context
    runner.stdout_bytes = (b"line\n" * 81) + "\u009b31m\u009dtitle".encode()

    result = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/output",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    assert result["stdout_preview"].startswith("[untrusted command output]\n")
    assert "\\x9b31m\\x9dtitle" in result["stdout_preview"]


def test_lab_exec_returns_explicit_output_limit_truth(lab_context) -> None:
    ctx, runner, _ordinary_runner = lab_context
    runner.output_limited = True

    result = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/output",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "SANDBOX_RESOURCE_LIMIT"
    assert result["terminated"] is True
    assert result["output_limited"] is True
    assert result["stdout_truncated"] is True
    assert result["limit_reason"] == "output"


def test_lab_exec_reports_missing_canonical_evidence(lab_context, monkeypatch) -> None:
    ctx, _runner, _ordinary_runner = lab_context

    def fail_ingest(**_kwargs: object) -> None:
        raise RuntimeError("cas unavailable")

    monkeypatch.setattr(ctx.artifactctl, "ingest_bytes", fail_ingest)
    result = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/defect",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "EVIDENCE_UNAVAILABLE"
    assert result.get("evidence_artifact") is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"command": "curl http://127.0.0.1", "include_evidence_artifact": False},
        {"command": "curl http://127.0.0.1 | cat", "include_evidence_artifact": True},
        {"command": "cd /tmp && curl localhost", "include_evidence_artifact": True},
        {"command": "TOKEN=x curl localhost", "include_evidence_artifact": True},
        {"command": "curl $(whoami)", "include_evidence_artifact": True},
        {"command": "sh -c whoami", "include_evidence_artifact": True},
    ],
)
def test_lab_exec_denials_never_touch_either_runner(lab_context, arguments) -> None:
    ctx, runner, ordinary_runner = lab_context
    result = _h_exec_run(arguments, ctx)
    assert result["status"] == "denied"
    assert runner.calls == []
    assert ordinary_runner.calls == []


def test_active_report_resolves_exec_evidence_and_keeps_v2_model_schema(
    lab_context,
) -> None:
    ctx, _runner, _ordinary_runner = lab_context
    execution = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/defect",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    evidence_ref = execution["evidence_artifact"]["ref"]
    ctx.tool_name = "security.publish_report"
    result = _h_publish_report(
        {
            "activity_class": "local_lab_active",
            "objective": "Validate the synthetic authorization boundary.",
            "validation_evidence": [evidence_ref],
            "findings": [
                {
                    "finding_id": "LAB-1",
                    "disposition": "candidate",
                    "title": "Synthetic route returned data",
                    "category": "authorization",
                    "severity": "medium",
                    "confidence": "high",
                    "explanation": "The approved request reached the observable route.",
                    "evidence_ref": evidence_ref,
                    "location": {
                        "component": "synthetic-target",
                        "path": "/defect",
                        "method": "GET",
                    },
                }
            ],
            "summary": "One candidate finding was observed.",
            "limitations": "Synthetic target only.",
        },
        ctx,
    )

    report = json.loads(ctx.artifactctl.read_bytes(result["report_ref"]))
    assert result["schema_version"] == "security-audit-report/v2"
    assert result["execution_status"] == "completed"
    assert report["findings"][0]["basis"] == "active_validation"
    assert report["target"]["container_id"] == "target-id"


def test_active_report_rejects_evidence_from_another_session(lab_context) -> None:
    ctx, _runner, _ordinary_runner = lab_context
    execution = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/defect",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    ctx.session_id = "session-2"
    ctx.tool_name = "security.publish_report"

    with pytest.raises(ToolRuntimeError, match="approved lab scope"):
        _h_publish_report(
            {
                "activity_class": "local_lab_active",
                "objective": "Reject evidence from another session.",
                "validation_evidence": [execution["evidence_artifact"]["ref"]],
                "findings": [],
                "summary": "No report should be published.",
                "limitations": "Synthetic target only.",
            },
            ctx,
        )


def test_active_report_rechecks_current_authorization(lab_context) -> None:
    ctx, _runner, _ordinary_runner = lab_context
    execution = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/defect",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    ctx.tool_name = "security.publish_report"
    ctx.security_lab_metadata = lambda: {
        "security_lab_state": "unavailable",
        "security_lab_reason": "security_lab activation is inactive",
    }

    with pytest.raises(ToolRuntimeError, match="activation is unavailable"):
        _h_publish_report(
            {
                "activity_class": "local_lab_active",
                "objective": "Reject publication after approval expires.",
                "validation_evidence": [execution["evidence_artifact"]["ref"]],
                "findings": [],
                "summary": "No report should be published.",
                "limitations": "Synthetic target only.",
            },
            ctx,
        )


def test_active_report_rejects_deleted_evidence(lab_context) -> None:
    ctx, _runner, _ordinary_runner = lab_context
    execution = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/defect",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    evidence_ref = execution["evidence_artifact"]["ref"]
    ctx.artifactctl.delete(evidence_ref)
    ctx.tool_name = "security.publish_report"

    with pytest.raises(ToolRuntimeError, match="unreadable|approved lab scope"):
        _h_publish_report(_active_report_payload([evidence_ref]), ctx)


def test_active_report_rejects_wrong_evidence_schema(lab_context) -> None:
    ctx, _runner, _ordinary_runner = lab_context
    artifact = ctx.write_artifact(
        "security/wrong-evidence.json",
        b'{"schema":"wrong"}',
        "application/json",
        durable=True,
    )
    evidence_ref = artifact.canonical_ref
    ctx.tool_name = "security.publish_report"

    with pytest.raises(ToolRuntimeError, match="approved lab scope"):
        _h_publish_report(_active_report_payload([evidence_ref]), ctx)


def test_active_report_marks_mixed_limit_evidence_partial(lab_context) -> None:
    ctx, runner, _ordinary_runner = lab_context
    completed = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/complete",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    runner.output_limited = True
    limited = _h_exec_run(
        {
            "command": "curl http://127.0.0.1:8080/limited",
            "timeout_s": 10,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    refs = [
        completed["evidence_artifact"]["ref"],
        limited["evidence_artifact"]["ref"],
    ]
    ctx.tool_name = "security.publish_report"

    result = _h_publish_report(_active_report_payload(refs), ctx)

    assert result["execution_status"] == "partial"


def test_active_report_schema_rejects_duplicates_unknown_refs_and_opaque_fields() -> (
    None
):
    ref = "artifact://sha256/" + "a" * 64
    with pytest.raises(ValidationError):
        SecurityLabPublishArgs.model_validate(
            {
                "activity_class": "local_lab_active",
                "objective": "test",
                "validation_evidence": [ref, ref],
                "findings": [],
                "summary": "summary",
            }
        )
    with pytest.raises(ValidationError):
        SecurityLabPublishArgs.model_validate(
            {
                "activity_class": "local_lab_active",
                "objective": "test",
                "validation_evidence": [ref],
                "findings": [],
                "summary": "summary",
                "daemon_id": "caller-controlled",
            }
        )


def test_active_report_schema_accepts_exact_upper_bounds() -> None:
    refs = [_lab_ref(index) for index in range(1, 21)]
    findings = []
    for index in range(50):
        finding = _lab_finding(refs[index % len(refs)], f"LAB-{index}")
        finding.update(
            title="t" * 300,
            category="c" * 120,
            explanation="e" * 4000,
            location={
                "component": "c" * 200,
                "path": "/" + "p" * 499,
                "method": "OPTIONS",
                "parameter": "p" * 120,
            },
        )
        findings.append(finding)

    parsed = SecurityLabPublishArgs.model_validate(
        {
            "activity_class": "local_lab_active",
            "objective": "o" * 1000,
            "validation_evidence": refs,
            "findings": findings,
            "summary": "s" * 4000,
            "limitations": "l" * 4000,
        }
    )

    assert len(parsed.validation_evidence) == 20
    assert len(parsed.findings) == 50


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objective", ""),
        ("objective", "o" * 1001),
        ("validation_evidence", []),
        ("validation_evidence", [_lab_ref(index) for index in range(1, 22)]),
        ("validation_evidence", ["not-a-canonical-ref"]),
        ("findings", [_lab_finding(_lab_ref(1), f"LAB-{i}") for i in range(51)]),
        ("summary", ""),
        ("summary", "s" * 4001),
        ("limitations", "l" * 4001),
    ],
)
def test_active_report_schema_rejects_out_of_bounds_publish_fields(
    field: str, value: object
) -> None:
    payload = _active_report_payload([_lab_ref(1)])
    payload[field] = value

    with pytest.raises(ValidationError):
        SecurityLabPublishArgs.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("finding_id", "bad id"),
        ("finding_id", "F" * 81),
        ("disposition", "validated"),
        ("title", ""),
        ("title", "t" * 301),
        ("category", ""),
        ("category", "c" * 121),
        ("severity", "urgent"),
        ("confidence", "certain"),
        ("explanation", ""),
        ("explanation", "e" * 4001),
        ("evidence_ref", "not-a-canonical-ref"),
    ],
)
def test_active_report_schema_rejects_invalid_finding_fields(
    field: str, value: object
) -> None:
    finding = _lab_finding(_lab_ref(1))
    finding[field] = value
    payload = _active_report_payload([_lab_ref(1)])
    payload["findings"] = [finding]

    with pytest.raises(ValidationError):
        SecurityLabPublishArgs.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("component", ""),
        ("component", "c" * 201),
        ("path", "relative"),
        ("path", "/" + "p" * 500),
        ("method", "TRACE"),
        ("parameter", ""),
        ("parameter", "p" * 121),
    ],
)
def test_active_report_schema_rejects_invalid_location_fields(
    field: str, value: object
) -> None:
    finding = _lab_finding(_lab_ref(1))
    location = dict(finding["location"])
    location[field] = value
    finding["location"] = location
    payload = _active_report_payload([_lab_ref(1)])
    payload["findings"] = [finding]

    with pytest.raises(ValidationError):
        SecurityLabPublishArgs.model_validate(payload)


def test_active_report_schema_rejects_duplicate_and_unknown_finding_refs() -> None:
    payload = _active_report_payload([_lab_ref(1)])
    payload["findings"] = [
        _lab_finding(_lab_ref(1), "LAB-1"),
        _lab_finding(_lab_ref(1), "LAB-1"),
    ]
    with pytest.raises(ValidationError, match="finding_id values must be unique"):
        SecurityLabPublishArgs.model_validate(payload)

    payload["findings"] = [_lab_finding(_lab_ref(2))]
    with pytest.raises(ValidationError, match="must be listed"):
        SecurityLabPublishArgs.model_validate(payload)
