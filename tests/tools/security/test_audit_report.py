from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.tool import ToolRegistry, ToolSpec
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.security.config import (
    resolve_local_target,
    resolve_security_config,
)
from openminion.tools.security.plugin import (
    _admit_researcher_scan,
    _h_publish_report,
    _handler,
)
from openminion.tools.security.providers.trivy import TRIVY_FIXED_FLAGS
from openminion.tools.security.schemas import (
    LocalScanArgs,
    ScanError,
    SecurityAuditPublishArgs,
    SecurityConfigurationIdentity,
    SecurityFinding,
    SecurityScanResult,
)
from tests.artifact.utils import make_config

_GIT = shutil.which("git")
_CHECK_TO_TOOL = {
    "code": "security.scan_code",
    "dependencies": "security.scan_dependencies",
    "iac": "security.scan_artifact",
    "secrets": "security.scan_secrets",
}
_SEMGREP_CONFIG_SHA256 = hashlib.sha256(b"rules: []\n").hexdigest()


def _git(*args: str, cwd: Path) -> str:
    assert _GIT is not None
    result = subprocess.run(  # noqa: S603 - test fixture with explicit argv
        (_GIT, *args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def audit_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "target"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
    rule = workspace / "rule.yml"
    rule.write_text("rules: []\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=workspace)
    _git("config", "user.email", "security-test@example.invalid", cwd=workspace)
    _git("config", "user.name", "Security Test", cwd=workspace)
    _git("config", "commit.gpgsign", "false", cwd=workspace)
    _git("add", ".", cwd=workspace)
    _git("commit", "-q", "-m", "fixture", cwd=workspace)

    run_root = tmp_path / "run"
    run_root.mkdir()
    home_root = tmp_path / "home"
    data_root = tmp_path / "cas"
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    ctl = ArtifactCtl(make_config(data_root))
    ctx = RuntimeContext(
        policy=Policy(raw={"workspace_root": str(workspace)}),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
        env=EnvironmentConfig(values={"OPENMINION_SECURITY_SEMGREP_CONFIG": str(rule)}),
        artifactctl=ctl,
        permission_mode="readonly",
        session_id="audit-session",
    )
    try:
        yield ctx, _git("rev-parse", "HEAD", cwd=workspace)
    finally:
        ctl.close()


def _scan(
    *,
    check: str,
    revision: str,
    status: str = "completed",
    truncated: bool = False,
    finding: bool = False,
) -> SecurityScanResult:
    findings = []
    if finding:
        findings.append(
            SecurityFinding(
                finding_id="scanner-finding-1",
                rule_id="test.rule",
                category="code",
                severity="ERROR",
                normalized_severity="high",
                message="test finding",
                location={"path": "app.py", "line": 1},
            )
        )
    scanner = "semgrep" if check == "code" else "trivy"
    return SecurityScanResult(
        ok=status in {"completed", "partial"},
        capability_id=_CHECK_TO_TOOL[check],
        adapter_id=scanner,
        scanner=scanner,
        scanner_version="1.0.0",
        target=".",
        target_revision=revision,
        permission_mode="readonly",
        configuration_identity=SecurityConfigurationIdentity(
            scanner_version="1.0.0",
            scan_mode="code" if check == "code" else "vuln",
            config_sha256=_SEMGREP_CONFIG_SHA256 if check == "code" else "",
            fixed_flags=[] if check == "code" else list(TRIVY_FIXED_FLAGS),
        ),
        started_at="2026-09-05T00:00:00+00:00",
        duration_ms=10,
        status=status,
        total_findings=len(findings),
        returned_findings=len(findings),
        truncated=truncated,
        findings=findings,
        error=(
            ScanError(code="EXEC_ERROR", message="scanner failed")
            if status not in {"completed", "partial"}
            else None
        ),
    )


def _store_scan(
    ctx: RuntimeContext,
    scan: SecurityScanResult,
    *,
    artifact_tool: str | None = None,
) -> str:
    return ctx.artifactctl.ingest_bytes(
        data=scan.model_dump_json(exclude_none=True).encode(),
        mime="application/json",
        original_name="scan.json",
        meta={"tool_name": artifact_tool or scan.capability_id},
        session_id=ctx.session_id,
    ).ref


def _publish_args(
    *,
    revision: str,
    refs: dict[str, str],
    findings: list[dict[str, Any]] | None = None,
    summary: str = "Candidate audit summary.",
) -> dict[str, Any]:
    return {
        "scope": {
            "target": ".",
            "target_revision": revision,
            "objective": "Review the approved source.",
            "requested_checks": list(refs),
            "activity_class": "source_readonly",
        },
        "check_evidence": [
            {"check": check, "evidence_ref": ref} for check, ref in refs.items()
        ],
        "findings": findings or [],
        "summary": summary,
        "limitations": "Static scanner evidence only.",
    }


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
@pytest.mark.parametrize(
    "status",
    ["completed", "partial", "failed", "timed_out", "cancelled", "unavailable"],
)
def test_researcher_scan_persists_every_terminal_status(
    audit_context, status: str
) -> None:
    ctx, revision = audit_context

    def scanner(args, *, target, config):
        del args, target, config
        return _scan(check="code", revision="", status=status)

    result = _handler(
        LocalScanArgs,
        scanner,
        requires_semgrep_config=True,
    )(
        {
            "target": ".",
            "expected_target_revision": revision,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    ref = result["evidence_refs"][0]
    assert ref.startswith("artifact://sha256/")
    assert result["artifact_refs"] == [ref]
    stored = SecurityScanResult.model_validate_json(ctx.artifactctl.read_bytes(ref))
    assert stored.status == status
    assert stored.target_revision == revision
    assert stored.permission_mode == "readonly"
    assert f"evidence={ref}" in result["content"]


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
def test_scan_evidence_compatibility_and_preflight_behavior(audit_context) -> None:
    ctx, revision = audit_context

    def failed_scanner(args, *, target, config):
        del args, target, config
        return _scan(check="code", revision="", status="failed")

    legacy = _handler(LocalScanArgs, failed_scanner)(
        {"target": ".", "include_evidence_artifact": True},
        ctx,
    )
    assert legacy["artifact_refs"] == []

    def unavailable_scanner(args, *, target, config):
        del args, target, config
        result = _scan(check="code", revision="", status="unavailable")
        result.configuration_identity = None
        result.scanner_version = ""
        return result

    preflight = _handler(LocalScanArgs, unavailable_scanner)(
        {
            "target": ".",
            "expected_target_revision": revision,
            "include_evidence_artifact": True,
        },
        ctx,
    )
    assert preflight["artifact_refs"] == []


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
def test_researcher_scan_redacts_persisted_error_text(audit_context) -> None:
    ctx, revision = audit_context

    def scanner(args, *, target, config):
        del args, target, config
        result = _scan(check="code", revision="", status="failed")
        assert result.error is not None
        result.error.message = "Bearer scanner-secret-123456"
        return result

    result = _handler(LocalScanArgs, scanner, requires_semgrep_config=True)(
        {
            "target": ".",
            "expected_target_revision": revision,
            "include_evidence_artifact": True,
        },
        ctx,
    )

    persisted = ctx.artifactctl.read_bytes(result["evidence_refs"][0]).decode()
    assert "scanner-secret" not in persisted
    assert "[REDACTED]" in persisted


def test_brain_tool_adapter_honors_ok_before_domain_status(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="security.fixture",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=lambda _args, _ctx: {
                "ok": True,
                "status": "completed",
                "content": "scan completed",
            },
        )
    )
    result = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
    ).execute(
        command={
            "tool_name": "security.fixture",
            "args": {},
            "inputs": {"permission_mode": "readonly"},
        },
        session_id="security-adapter",
        trace_id="trace-security-adapter",
    )

    assert result["status"] == "success"
    assert result["outputs"]["status"] == "completed"


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
def test_researcher_scan_refuses_permission_revision_dirt_and_non_file_rules(
    audit_context,
) -> None:
    ctx, revision = audit_context
    config = resolve_security_config(ctx)
    target = resolve_local_target(".", config)
    args = LocalScanArgs(
        target=".",
        expected_target_revision=revision,
        include_evidence_artifact=True,
    )

    ctx.permission_mode = "ask"
    with pytest.raises(ToolRuntimeError, match="require readonly"):
        _admit_researcher_scan(
            args,
            ctx=ctx,
            target=target,
            config=config,
            requires_semgrep_config=True,
        )
    ctx.permission_mode = "readonly"

    mismatched = args.model_copy(update={"expected_target_revision": "f" * 40})
    with pytest.raises(ToolRuntimeError, match="does not match"):
        _admit_researcher_scan(
            mismatched,
            ctx=ctx,
            target=target,
            config=config,
            requires_semgrep_config=True,
        )

    (ctx.workspace / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ToolRuntimeError, match="must be clean"):
        _admit_researcher_scan(
            args,
            ctx=ctx,
            target=target,
            config=config,
            requires_semgrep_config=True,
        )
    (ctx.workspace / "dirty.txt").unlink()

    ctx.env = EnvironmentConfig(
        values={"OPENMINION_SECURITY_SEMGREP_CONFIG": str(ctx.workspace)}
    )
    invalid_config = resolve_security_config(ctx)
    with pytest.raises(ToolRuntimeError, match="local Semgrep config file"):
        _admit_researcher_scan(
            args,
            ctx=ctx,
            target=target,
            config=invalid_config,
            requires_semgrep_config=True,
        )

    ctx.env = EnvironmentConfig(values={"OPENMINION_SECURITY_SEMGREP_CONFIG": "p/ci"})
    remote_config = resolve_security_config(ctx)
    with pytest.raises(ToolRuntimeError, match="local Semgrep config file"):
        _admit_researcher_scan(
            args,
            ctx=ctx,
            target=target,
            config=remote_config,
            requires_semgrep_config=True,
        )


@pytest.mark.parametrize(
    ("statuses", "truncated", "expected"),
    [
        (("completed",), False, "completed"),
        (("completed",), True, "partial"),
        (("partial",), False, "partial"),
        (("completed", "failed"), False, "partial"),
        (("failed", "timed_out"), False, "blocked"),
        (("cancelled", "unavailable"), False, "blocked"),
    ],
)
def test_report_derives_execution_status(
    audit_context,
    statuses: tuple[str, ...],
    truncated: bool,
    expected: str,
) -> None:
    ctx, revision = audit_context
    check_names = ("code", "dependencies")[: len(statuses)]
    refs = {
        check: _store_scan(
            ctx,
            _scan(
                check=check,
                revision=revision,
                status=status,
                truncated=truncated,
            ),
        )
        for check, status in zip(check_names, statuses, strict=True)
    }

    result = _h_publish_report(_publish_args(revision=revision, refs=refs), ctx)
    report = json.loads(ctx.artifactctl.read_bytes(result["report_ref"]))

    assert result["execution_status"] == expected
    assert result["review_status"] == "unreviewed"
    assert result["artifact_refs"] == [result["report_ref"]]
    assert report["execution_status"] == expected
    assert report["review_status"] == "unreviewed"


def test_report_accepts_version_check_unavailable_as_terminal_evidence(
    audit_context,
) -> None:
    ctx, revision = audit_context
    scan = _scan(check="code", revision=revision, status="unavailable")
    scan.scanner_version = ""
    assert scan.configuration_identity is not None
    scan.configuration_identity = scan.configuration_identity.model_copy(
        update={"scanner_version": ""}
    )
    ref = _store_scan(ctx, scan)

    result = _h_publish_report(
        _publish_args(revision=revision, refs={"code": ref}),
        ctx,
    )

    assert result["execution_status"] == "blocked"


def test_report_requires_exact_canonical_evidence_cardinality(audit_context) -> None:
    _ctx, revision = audit_context
    base = _publish_args(
        revision=revision,
        refs={"code": "artifact://sha256/" + ("a" * 64)},
    )
    base["scope"]["requested_checks"] = ["code", "dependencies"]
    with pytest.raises(ValidationError, match="exactly match"):
        SecurityAuditPublishArgs.model_validate(base)

    duplicate = _publish_args(
        revision=revision,
        refs={"code": "artifact://sha256/" + ("a" * 64)},
    )
    duplicate["check_evidence"].append(duplicate["check_evidence"][0])
    with pytest.raises(ValidationError, match="one ref per check"):
        SecurityAuditPublishArgs.model_validate(duplicate)

    local = _publish_args(revision=revision, refs={"code": "security/scan.json"})
    with pytest.raises(ValidationError):
        SecurityAuditPublishArgs.model_validate(local)

    encoded_scope = _publish_args(
        revision=revision,
        refs={"code": "artifact://sha256/" + ("a" * 64)},
    )
    encoded_scope["scope"] = json.dumps(encoded_scope["scope"])
    parsed = SecurityAuditPublishArgs.model_validate(encoded_scope)
    assert parsed.scope.target_revision == revision


def test_report_rejects_mismatched_or_deleted_scan_evidence(audit_context) -> None:
    ctx, revision = audit_context
    mismatch_ref = _store_scan(
        ctx,
        _scan(check="dependencies", revision=revision),
        artifact_tool="security.scan_code",
    )
    with pytest.raises(ToolRuntimeError, match="does not match"):
        _h_publish_report(
            _publish_args(revision=revision, refs={"code": mismatch_ref}),
            ctx,
        )

    deleted_ref = _store_scan(ctx, _scan(check="code", revision=revision))
    ctx.artifactctl.delete(deleted_ref)
    with pytest.raises(ToolRuntimeError, match="deleted"):
        _h_publish_report(
            _publish_args(revision=revision, refs={"code": deleted_ref}),
            ctx,
        )


def test_report_requires_same_session_tool_and_coherent_scanner_identity(
    audit_context,
) -> None:
    ctx, revision = audit_context
    scan = _scan(check="code", revision=revision)
    wrong_session_ref = ctx.artifactctl.ingest_bytes(
        data=scan.model_dump_json(exclude_none=True).encode(),
        mime="application/json",
        original_name="scan.json",
        meta={"tool_name": scan.capability_id},
        session_id="another-session",
    ).ref
    with pytest.raises(ToolRuntimeError, match="audit session and tool"):
        _h_publish_report(
            _publish_args(revision=revision, refs={"code": wrong_session_ref}),
            ctx,
        )

    wrong_tool_scan = scan.model_copy(update={"duration_ms": 11})
    wrong_tool_ref = ctx.artifactctl.ingest_bytes(
        data=wrong_tool_scan.model_dump_json(exclude_none=True).encode(),
        mime="application/json",
        original_name="scan.json",
        meta={"tool_name": "security.scan_dependencies"},
        session_id=ctx.session_id,
    ).ref
    with pytest.raises(ToolRuntimeError, match="audit session and tool"):
        _h_publish_report(
            _publish_args(revision=revision, refs={"code": wrong_tool_ref}),
            ctx,
        )

    assert scan.configuration_identity is not None
    scan.configuration_identity = scan.configuration_identity.model_copy(
        update={"scan_mode": "secret"}
    )
    incoherent_ref = _store_scan(ctx, scan)
    with pytest.raises(ToolRuntimeError, match="does not match"):
        _h_publish_report(
            _publish_args(revision=revision, refs={"code": incoherent_ref}),
            ctx,
        )


def test_report_validates_scanner_finding_links_and_redacts_text(audit_context) -> None:
    ctx, revision = audit_context
    ref = _store_scan(ctx, _scan(check="code", revision=revision, finding=True))
    finding = {
        "finding_id": "candidate-1",
        "basis": "scanner",
        "disposition": "candidate",
        "title": "Token leak",
        "category": "credential",
        "severity": "api_key=SEVERITY_SECRET",
        "confidence": "Bearer confidence-secret-12345",
        "description": "api_key=TOP_SECRET_VALUE",
        "impact": "Credential exposure.",
        "validation": "Review the scanner evidence.",
        "recommendation": "Remove the credential.",
        "locations": [{"path": "app.py", "line": 1}],
        "evidence_ref": ref,
        "scanner_finding_ids": ["scanner-finding-1"],
    }

    result = _h_publish_report(
        _publish_args(
            revision=revision,
            refs={"code": ref},
            findings=[finding],
            summary="Bearer abcdefghijklmnop is exposed.",
        ),
        ctx,
    )
    report = json.loads(ctx.artifactctl.read_bytes(result["report_ref"]))

    assert result["candidate_count"] == 1
    assert result["redaction_count"] == 4
    assert "TOP_SECRET_VALUE" not in json.dumps(report)
    assert "abcdefghijklmnop" not in json.dumps(report)
    assert "SEVERITY_SECRET" not in json.dumps(report)
    assert "confidence-secret" not in json.dumps(report)

    finding["scanner_finding_ids"] = ["missing"]
    with pytest.raises(ToolRuntimeError, match="must exist"):
        _h_publish_report(
            _publish_args(
                revision=revision,
                refs={"code": ref},
                findings=[finding],
            ),
            ctx,
        )


def test_report_schema_rejects_absolute_manual_evidence_and_review_override(
    audit_context,
) -> None:
    _ctx, revision = audit_context
    payload = _publish_args(
        revision=revision,
        refs={"code": "artifact://sha256/" + ("a" * 64)},
        findings=[
            {
                "finding_id": "manual-1",
                "basis": "manual",
                "disposition": "candidate",
                "title": "Manual candidate",
                "category": "logic",
                "severity": "medium",
                "confidence": "low",
                "description": "Review this path.",
                "locations": [{"path": "/tmp/source.py"}],
                "evidence_summary": "Read-only code inspection.",
            }
        ],
    )
    payload["review_status"] = "validated"

    with pytest.raises(ValidationError):
        SecurityAuditPublishArgs.model_validate(payload)


def test_report_fails_when_final_canonical_ingest_fails(audit_context) -> None:
    ctx, revision = audit_context
    ref = _store_scan(ctx, _scan(check="code", revision=revision))
    real_ctl = ctx.artifactctl

    class FailingIngest:
        def get(self, value):
            return real_ctl.get(value)

        def read_bytes(self, value):
            return real_ctl.read_bytes(value)

        def ingest_bytes(self, **kwargs):
            del kwargs
            raise OSError("CAS offline")

    ctx.artifactctl = FailingIngest()
    with pytest.raises(ToolRuntimeError, match="did not produce a canonical"):
        _h_publish_report(
            _publish_args(revision=revision, refs={"code": ref}),
            ctx,
        )
