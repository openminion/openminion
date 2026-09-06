"""Security tool handlers over concrete scanner adapters."""

import json
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from openminion.base.redaction import redact_mapping
from openminion.modules.tool import preferred_artifact_ref
from openminion.modules.tool.errors import ToolRuntimeError

from .config import SecurityConfig, resolve_local_target, resolve_security_config
from .providers import scan_artifact, scan_code, scan_dependencies, scan_secrets
from .report import publish_security_audit
from .schemas import (
    LocalScanArgs,
    SecurityAuditPublishArgs,
    SecurityScanResult,
)

Scanner = Callable[..., SecurityScanResult]


def _handler(
    args_model: type[LocalScanArgs],
    scanner: Scanner,
    *,
    requires_semgrep_config: bool = False,
):
    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        parsed = args_model.model_validate(args)
        config = resolve_security_config(ctx)
        target = resolve_local_target(parsed.target, config)
        config, target_revision = _admit_researcher_scan(
            parsed,
            ctx=ctx,
            target=target,
            config=config,
            requires_semgrep_config=requires_semgrep_config,
        )
        result = scanner(parsed, target=target, config=config)
        if target_revision:
            result.target_revision = target_revision
            result.permission_mode = "readonly"
        preflight_unavailable = (
            bool(target_revision)
            and result.status == "unavailable"
            and result.configuration_identity is None
        )
        persist_evidence = parsed.include_evidence_artifact and (
            bool(target_revision) or result.status in {"completed", "partial"}
        )
        if persist_evidence and not preflight_unavailable:
            artifact_payload, _ = redact_mapping(
                result.model_dump(mode="json", exclude_none=True)
            )
            artifact = ctx.write_artifact(
                f"security/{uuid.uuid4().hex}.json",
                json.dumps(
                    artifact_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                ).encode(),
                "application/json",
                durable=True,
            )
            artifact_ref = preferred_artifact_ref(artifact)
            if target_revision and not artifact_ref.startswith("artifact://sha256/"):
                raise ToolRuntimeError(
                    "INTERNAL_ERROR",
                    "security scan evidence requires canonical artifact storage",
                )
            result.evidence_refs.append(artifact_ref)
        return result.as_tool_result()

    return handler


def _admit_researcher_scan(
    args: LocalScanArgs,
    *,
    ctx: Any,
    target: Path,
    config: SecurityConfig,
    requires_semgrep_config: bool,
) -> tuple[SecurityConfig, str]:
    expected = args.expected_target_revision
    if expected is None:
        return config, ""
    if str(getattr(ctx, "permission_mode", "") or "") != "readonly":
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security researcher scans require readonly permission mode",
            {"reason_code": "security_researcher_requires_readonly"},
        )
    if not args.include_evidence_artifact:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security researcher scans require canonical evidence",
            {"reason_code": "security_researcher_requires_evidence"},
        )

    workspace = config.workspace_root.resolve()
    repo_root = Path(_git_output(workspace, "rev-parse", "--show-toplevel")).resolve()
    if repo_root != workspace:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security researcher target must use the Git worktree root as workspace",
            {"reason_code": "security_researcher_workspace_mismatch"},
        )
    try:
        target.relative_to(repo_root)
    except ValueError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security researcher target is outside the Git worktree",
            {"reason_code": "security_researcher_target_outside_worktree"},
        ) from exc
    if _git_output(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security researcher target worktree must be clean",
            {"reason_code": "security_researcher_dirty_worktree"},
        )
    observed = _git_output(workspace, "rev-parse", "HEAD").lower()
    if observed != expected.lower():
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security researcher target revision does not match",
            {
                "reason_code": "security_researcher_revision_mismatch",
                "expected_revision": expected.lower(),
                "observed_revision": observed,
            },
        )
    if requires_semgrep_config:
        config = replace(config, semgrep_config=_local_semgrep_config(config))
    return config, observed


def _git_output(workspace: Path, *args: str) -> str:
    process = subprocess.run(  # noqa: S603, S607 - fixed read-only Git commands
        ("git", "-C", str(workspace), *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if process.returncode != 0:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security researcher target must be a readable Git worktree",
            {"reason_code": "security_researcher_git_unavailable"},
        )
    return process.stdout.strip()


def _local_semgrep_config(config: SecurityConfig) -> str:
    path = Path(config.semgrep_config).expanduser()
    if not path.is_absolute():
        path = config.workspace_root / path
    try:
        resolved = path.resolve(strict=True)
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security researcher scans require one readable local Semgrep config file",
            {"reason_code": "security_researcher_invalid_semgrep_config"},
        ) from exc
    if not resolved.is_file():
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security researcher scans require one readable local Semgrep config file",
            {"reason_code": "security_researcher_invalid_semgrep_config"},
        )
    return str(resolved)


_h_scan_code = _handler(LocalScanArgs, scan_code, requires_semgrep_config=True)
_h_scan_dependencies = _handler(LocalScanArgs, scan_dependencies)
_h_scan_artifact = _handler(LocalScanArgs, scan_artifact)
_h_scan_secrets = _handler(LocalScanArgs, scan_secrets)


def _h_publish_report(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return publish_security_audit(
        SecurityAuditPublishArgs.model_validate(args), ctx=ctx
    )


__all__ = [
    "_h_scan_artifact",
    "_h_scan_code",
    "_h_scan_dependencies",
    "_h_scan_secrets",
    "_h_publish_report",
]
