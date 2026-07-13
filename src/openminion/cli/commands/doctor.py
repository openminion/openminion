from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.config import (
    resolve_runtime_profile,
    run_profile_overrides_from_mapping,
)
from openminion.base.config.runtime.capability import resolve_plugin_runtime_policy
from openminion.cli.identity.provenance import build_identity_provenance
from openminion.cli.config import (
    load_cli_manager,
    resolve_cli_identity_db_path,
    resolve_identity_bundle_root,
)
from openminion.base.types import Message
from openminion.modules.llm.providers.factory import SUPPORTED_PROVIDERS
from openminion.modules.identity import load_identity_bundle
from openminion.services.health.probes import (
    ProbeResult,
    probe_channels_enabled,
    probe_config_exists,
    probe_default_channel_in_enabled,
    probe_plugins_enabled,
    probe_provider_key,
    probe_provider_session,
    probe_provider_supported,
    probe_runtime_bootstrap,
    probe_storage_ready,
)
from openminion.services.security.validate import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARN,
    SecurityValidateFinding,
    run_security_validate,
)
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.services.health.service import (
    _evaluate_supervision_decision,
    _load_lifecycle_facts,
    _supervision_policies_for_lifecycle_facts,
)
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload

_DOCTOR_LOG = logging.getLogger("openminion.doctor")

_CHECK_STATUS_WARN: str = "warn"


@dataclass
class DoctorCheck:
    id: str
    status: str
    message: str
    remediation: str = ""
    severity: str = ""
    details: dict[str, Any] | None = None
    finding_id: str = ""
    summary: str = ""
    target_component: dict[str, Any] | None = None
    related_probe_ids: list[str] | None = None
    recommended_actions: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
        }
        if self.finding_id:
            payload["finding_id"] = self.finding_id
        if self.summary:
            payload["summary"] = self.summary
        if self.severity:
            payload["severity"] = self.severity
        if self.remediation:
            payload["remediation"] = self.remediation
        if self.target_component:
            payload["target_component"] = self.target_component
        if self.related_probe_ids:
            payload["related_probe_ids"] = self.related_probe_ids
        if self.recommended_actions:
            payload["recommended_actions"] = self.recommended_actions
        if self.details:
            payload["details"] = self.details
        return payload


def _doctor_check_from_probe(probe: ProbeResult) -> DoctorCheck:
    return DoctorCheck(
        id=probe.id,
        status=probe.status,
        message=probe.message,
        remediation=probe.remediation,
        details=probe.details,
    )


def _collect_pre_runtime_checks(
    *,
    args,
    config,
    config_path: Path,
    manager,
    selected_agent,
    provider_name: str,
    storage_path,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(_doctor_check_from_probe(probe_config_exists(config_path)))
    checks.append(
        _build_identity_bundle_check(
            config=config,
            config_path=config_path,
            agent_id=selected_agent.name,
            home_root=manager.home_root,
            data_root=manager.data_root,
        )
    )
    checks.append(_doctor_check_from_probe(probe_storage_ready(storage_path).probe))
    checks.append(
        _doctor_check_from_probe(
            probe_provider_supported(
                provider_name=provider_name,
                supported_providers=SUPPORTED_PROVIDERS,
            )
        )
    )
    provider_key_probe = probe_provider_key(config=config, provider_name=provider_name)
    if provider_key_probe is not None:
        checks.append(_doctor_check_from_probe(provider_key_probe))
    provider_session_probe = probe_provider_session(
        config=config,
        provider_name=provider_name,
    )
    if provider_session_probe is not None:
        checks.append(_doctor_check_from_probe(provider_session_probe))
    checks.append(_build_skill_module_check())
    enabled_channels = list(config.enabled_channels)
    checks.append(_doctor_check_from_probe(probe_channels_enabled(enabled_channels)))
    checks.append(
        _doctor_check_from_probe(
            probe_default_channel_in_enabled(
                default_channel=selected_agent.default_channel,
                enabled_channels=enabled_channels,
            )
        )
    )
    checks.append(
        _doctor_check_from_probe(
            probe_plugins_enabled(
                resolve_plugin_runtime_policy(
                    compatibility_enabled_plugins=list(config.enabled_plugins),
                    system_policy=getattr(config.runtime, "plugins", None),
                ).effective_enabled
            )
        )
    )
    if not bool(getattr(args, "skip_supervision", False)):
        checks.extend(
            _build_supervision_checks(
                home_root=manager.home_root,
            )
        )
    return checks


def _run_runtime_bootstrap_probe(
    *,
    args,
    selected_agent,
    provider_name: str,
    run_profile_overrides,
) -> tuple[DoctorCheck, APIRuntime | None, list[str], list[str]]:
    app: APIRuntime | None = None
    loaded_plugin_manifest_ids: list[str] = []
    loaded_tool_names: list[str] = []

    def _runtime_bootstrap_details() -> dict[str, Any]:
        nonlocal app, loaded_plugin_manifest_ids, loaded_tool_names
        app = APIRuntime.from_config_path(
            args.config,
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
            run_profile_overrides=run_profile_overrides,
        )
        selected_agent_service = app.resolve_agent_service(selected_agent.name)
        loaded_plugin_manifest_ids = app.plugins.manifest_ids()
        loaded_tool_names = [spec.name for spec in app.tools.provider_specs()]
        runtime_posture = app.runtime_posture(
            agent_id=selected_agent.name,
            overrides=run_profile_overrides,
        )
        capability_report = app.capability_report(
            agent_id=selected_agent.name,
            overrides=run_profile_overrides,
        )

        del selected_agent_service
        return {
            "agent": selected_agent.name,
            "provider": provider_name,
            "loaded_channels": app.channels.names(),
            "loaded_plugins": app.plugins.names(),
            "plugin_manifest_ids": loaded_plugin_manifest_ids,
            "tool_names": loaded_tool_names,
            "agent_runtime_mode": runtime_posture["runtime_mode"],
            "brain_bridge_active": runtime_posture["brain_bridge_active"],
            "last_bridge_fallback_reason": runtime_posture["fallback_reason"],
            "runtime_posture": runtime_posture,
            "capability_layering": app.capability_runtime_diagnostics(
                agent_id=selected_agent.name,
                overrides=run_profile_overrides,
            ),
            "capabilities": capability_report,
        }

    check = _doctor_check_from_probe(
        probe_runtime_bootstrap(
            bootstrap_fn=_runtime_bootstrap_details,
            success_message="Runtime context bootstrapped successfully",
            failure_remediation="Fix provider/config/channel/plugin issues and re-run doctor.",
        )
    )
    return check, app, loaded_plugin_manifest_ids, loaded_tool_names


def _append_security_validate_checks(
    *,
    checks: list[DoctorCheck],
    config,
    config_path: Path,
    storage_path,
    app: APIRuntime | None,
    loaded_plugin_manifest_ids: list[str],
    loaded_tool_names: list[str],
) -> None:
    security_report = run_security_validate(
        config=config,
        config_path=config_path,
        storage_path=storage_path,
        loaded_plugin_manifest_ids=loaded_plugin_manifest_ids,
        loaded_plugin_manifests=app.plugins.manifests() if app is not None else [],
        loaded_tool_names=loaded_tool_names,
    )
    for finding in security_report.findings:
        checks.append(_doctor_check_from_security_finding(finding))
    checks.append(
        DoctorCheck(
            id="security.validate.summary",
            status=security_report.status,
            severity=(
                SEVERITY_CRITICAL
                if security_report.status == "fail"
                else SEVERITY_WARN
                if security_report.status == _CHECK_STATUS_WARN
                else SEVERITY_INFO
            ),
            message=(
                "Security validate summary: "
                f"critical={security_report.critical_count}, "
                f"warn={security_report.warn_count}, "
                f"info={security_report.info_count}"
            ),
            remediation=(
                "Address critical and warning findings in security validation report."
                if security_report.status in {"fail", "warn"}
                else ""
            ),
            details={
                "critical": security_report.critical_count,
                "warn": security_report.warn_count,
                "info": security_report.info_count,
            },
        )
    )


def _build_doctor_summary(
    *, checks: list[DoctorCheck], selected_agent, provider_name: str
) -> tuple[dict[str, Any], int, int, int]:
    fail_count = sum(1 for check in checks if check.status == "fail")
    warn_count = sum(1 for check in checks if check.status == _CHECK_STATUS_WARN)
    ok_count = sum(1 for check in checks if check.status == "ok")
    summary = {
        "status": "fail" if fail_count else "ok",
        "ok": fail_count == 0,
        "counts": {"ok": ok_count, "warn": warn_count, "fail": fail_count},
        "agent": selected_agent.name,
        "provider": provider_name,
        "default_channel": selected_agent.default_channel,
    }
    return summary, ok_count, warn_count, fail_count


def _render_doctor_output(
    *,
    args,
    summary: dict[str, Any],
    checks: list[DoctorCheck],
    ok_count: int,
    warn_count: int,
    fail_count: int,
) -> None:
    if args.json:
        print_json_payload(
            {"summary": summary, "checks": [check.to_dict() for check in checks]}
        )
        return
    print(
        f"doctor: {summary['status'].upper()} "
        f"(ok={ok_count} warn={warn_count} fail={fail_count})"
    )
    for check in checks:
        tag = check.status.upper()
        print(f"[{tag}] {check.id}: {check.message}")
        if check.remediation:
            print(f"  remediation: {check.remediation}")


def run_doctor(args) -> int:
    manager = load_cli_manager(
        args.config,
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config = manager.base_config
    config_path = manager.config_path
    run_profile_overrides = run_profile_overrides_from_mapping(vars(args))
    selected_agent = resolve_runtime_profile(
        config,
        agent_id=getattr(args, "agent_id", None),
        overrides=run_profile_overrides,
    )
    storage_path = resolve_database_path(config.storage.path)
    provider_name = (selected_agent.provider or "echo").strip().lower() or "echo"

    checks = _collect_pre_runtime_checks(
        args=args,
        config=config,
        config_path=config_path,
        manager=manager,
        selected_agent=selected_agent,
        provider_name=provider_name,
        storage_path=storage_path,
    )

    bootstrap_check, app, loaded_plugin_manifest_ids, loaded_tool_names = (
        _run_runtime_bootstrap_probe(
            args=args,
            selected_agent=selected_agent,
            provider_name=provider_name,
            run_profile_overrides=run_profile_overrides,
        )
    )
    checks.append(bootstrap_check)

    if args.check_turn:
        if app is None:
            checks.append(
                DoctorCheck(
                    id="agent.turn_smoke",
                    status="fail",
                    message="Skipped turn smoke test because runtime bootstrap failed",
                    remediation="Resolve runtime bootstrap issues first.",
                )
            )
        else:
            checks.append(
                _run_turn_smoke_check(
                    app=app,
                    message=args.message,
                    target=args.target,
                    channel=args.channel,
                    agent_id=selected_agent.name,
                )
            )

    _append_security_validate_checks(
        checks=checks,
        config=config,
        config_path=config_path,
        storage_path=storage_path,
        app=app,
        loaded_plugin_manifest_ids=loaded_plugin_manifest_ids,
        loaded_tool_names=loaded_tool_names,
    )

    for check in checks:
        _apply_diagnostics_contract_fields(
            check=check,
            provider_name=provider_name,
            default_channel=selected_agent.default_channel,
            storage_path=str(storage_path),
        )

    if app is not None:
        app.close()

    summary, ok_count, warn_count, fail_count = _build_doctor_summary(
        checks=checks,
        selected_agent=selected_agent,
        provider_name=provider_name,
    )
    _emit_doctor_normalization_telemetry(
        checks=checks,
        summary=summary,
    )
    _render_doctor_output(
        args=args,
        summary=summary,
        checks=checks,
        ok_count=ok_count,
        warn_count=warn_count,
        fail_count=fail_count,
    )
    return 1 if fail_count else 0


def _build_identity_bundle_check(
    *,
    config,
    config_path: Path,
    agent_id: str,
    home_root: Path,
    data_root: Path,
) -> DoctorCheck:
    from openminion.modules.identity.runtime.service import IdentityCtl
    from openminion.modules.identity.storage.store import SQLiteIdentityStore

    identity_db_path = resolve_cli_identity_db_path(
        config,
        home_root=home_root,
        data_root=data_root,
    )
    identity_db_path.parent.mkdir(parents=True, exist_ok=True)

    root = (
        Path(resolve_identity_bundle_root(config)).expanduser().resolve()
        if str(resolve_identity_bundle_root(config) or "").strip()
        else (config_path.parent if config_path.parent else home_root)
    )
    bundle = load_identity_bundle(agent_id=agent_id, root=root)
    bundle_details = {
        "root_path": bundle.root_path,
        "fingerprint": bundle.fingerprint,
        "skills_count": len(bundle.skills),
        "notes_count": len(bundle.notes),
        "warnings": list(bundle.warnings),
        "errors": list(bundle.errors),
    }

    identityctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(identity_db_path))
    )
    try:
        profile = identityctl.get_profile(agent_id)
    finally:
        identityctl.close()
    provenance = build_identity_provenance(profile)

    details: dict[str, Any] = {
        "identity_db_path": str(identity_db_path),
        "profile_present": bool(profile is not None),
        "profile_revision": int(getattr(profile, "profile_revision", 0))
        if profile is not None
        else None,
        "bundle_imported": bool(
            (getattr(profile, "meta", {}) or {}).get("bundle_imported")
        )
        if profile is not None
        else False,
        "bundle_fingerprint": str(
            (getattr(profile, "meta", {}) or {}).get("bundle_fingerprint") or ""
        )
        if profile is not None
        else "",
        **provenance,
        "bundle_diagnostics": bundle_details,
    }

    if profile is None:
        return DoctorCheck(
            id="identity.bundle",
            status="warn",
            message=f"Identity profile is missing in IdentityCtl for agent '{agent_id}'.",
            remediation=(
                "Create/import a profile via `openminion identity upsert ...` "
                "or configure bundle import at startup."
            ),
            details=details,
        )

    message = (
        f"Identity profile is present in IdentityCtl for agent '{agent_id}' "
        f"(revision={profile.profile_revision})"
    )
    if bundle.warnings or bundle.errors:
        return DoctorCheck(
            id="identity.bundle",
            status="warn",
            message=(
                message
                + "; bundle diagnostics reported issues (non-authoritative for runtime)."
            ),
            remediation="Review bundle diagnostics in details.bundle_diagnostics.",
            details=details,
        )

    return DoctorCheck(
        id="identity.bundle",
        status="ok",
        message=message,
        details=details,
    )


def _build_skill_module_check() -> DoctorCheck:
    try:
        from openminion.cli.commands.skill import _check_skill_available

        if _check_skill_available():
            return DoctorCheck(
                id="skill.module.available",
                status="ok",
                message="openminion.modules.skill module is available",
            )
        else:
            from openminion.cli.commands.skill import _get_skill_error

            return DoctorCheck(
                id="skill.module.available",
                status="warn",
                message="openminion.modules.skill module is not available",
                remediation=_get_skill_error(),
            )
    except Exception as exc:
        return DoctorCheck(
            id="skill.module.available",
            status="warn",
            message=f"Could not check skill module availability: {exc}",
            remediation="Skill commands may not work without openminion.modules.skill installed.",
        )


def _apply_diagnostics_contract_fields(
    *,
    check: DoctorCheck,
    provider_name: str,
    default_channel: str,
    storage_path: str,
) -> None:
    check.finding_id = check.id
    check.summary = check.message
    check.severity = _normalize_diagnostics_severity(
        current=check.severity,
        status=check.status,
    )
    if check.target_component is None:
        check.target_component = _target_component_for_check_id(
            check_id=check.id,
            provider_name=provider_name,
            default_channel=default_channel,
            storage_path=storage_path,
        )
    probe_related_ids = _probe_related_ids_for_check_id(check.id)
    if probe_related_ids:
        check.related_probe_ids = probe_related_ids
    if check.remediation:
        check.recommended_actions = [
            {
                "action_id": f"doctor.remediate.{check.id.replace('.', '_')}",
                "risk": "low" if check.status != "fail" else "medium",
                "description": check.remediation,
            }
        ]


def _build_supervision_checks(*, home_root: Path) -> list[DoctorCheck]:
    lifecycle_facts = _load_lifecycle_facts(home_root=home_root)
    observed_at = datetime.now(tz=timezone.utc).isoformat()
    checks: list[DoctorCheck] = []
    for component, policy in _supervision_policies_for_lifecycle_facts(lifecycle_facts):
        key = ":".join(
            [
                str(component.get("component_kind") or "").strip(),
                str(component.get("component_id") or "").strip(),
                str(component.get("scope") or "").strip(),
            ]
        )
        lifecycle_fact = lifecycle_facts.get(key)
        if lifecycle_fact is None:
            continue
        decision = _evaluate_supervision_decision(
            component=component,
            lifecycle_fact=lifecycle_fact,
            policy=policy,
            observed_at=observed_at,
        )
        if decision is None:
            continue
        normalized_posture = str(decision.posture or "").strip().lower()
        status = "ok"
        if normalized_posture == "degraded":
            status = "warn"
        elif normalized_posture == "failed":
            status = "fail"
        component_kind = (
            str(component.get("component_kind") or "").strip() or "component"
        )
        component_id = str(component.get("component_id") or "").strip() or "primary"
        remediation = ""
        if decision.restart.action == "disabled":
            remediation = "Automated restart is disabled for this supervision path; inspect and recover manually."
        elif decision.restart.action == "suppressed":
            remediation = f"Automated restart is suppressed ({decision.restart.reason}); investigate repeated failures before retrying."
        elif decision.restart.action in {"backoff", "restart_now"}:
            remediation = f"Supervision restart policy is active ({decision.restart.reason}); check lifecycle telemetry for restart follow-up."
        checks.append(
            DoctorCheck(
                id=f"supervision.{component_kind}.{component_id}",
                status=status,
                message=(
                    f"Supervision posture for {component_kind}/{component_id}: "
                    f"{decision.posture} ({decision.reason})"
                ),
                remediation=remediation,
                details={
                    "alert_level": decision.alert_level,
                    "restart_action": decision.restart.action,
                    "restart_reason": decision.restart.reason,
                    "restart_attempts": decision.restart_attempts,
                    "consecutive_failures": decision.consecutive_failures,
                    "last_exit_reason": decision.last_exit_reason,
                },
                target_component=dict(component),
            )
        )
    return checks


def _diagnostics_severity_from_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "fail":
        return "ERROR"
    if normalized == _CHECK_STATUS_WARN:
        return "WARN"
    return "OK"


def _normalize_diagnostics_severity(*, current: str, status: str) -> str:
    normalized = str(current or "").strip().upper()
    if normalized in {"OK", "INFO", "WARN", "ERROR", "CRITICAL"}:
        return normalized
    if normalized == "FAIL":
        return "ERROR"
    if normalized == "WARNING":
        return "WARN"
    if normalized == "CRIT":
        return "CRITICAL"
    return _diagnostics_severity_from_status(status)


def _target_component_for_check_id(
    *,
    check_id: str,
    provider_name: str,
    default_channel: str,
    storage_path: str,
) -> dict[str, Any]:
    normalized = str(check_id or "").strip().lower()
    if normalized.startswith("storage."):
        return {
            "component_kind": "storage_backend",
            "component_id": "sqlite-main",
            "scope": "system",
            "owner_module": "openminion-storage",
            "labels": {"path": storage_path},
        }
    if normalized.startswith("provider."):
        return {
            "component_kind": "provider_binding",
            "component_id": provider_name or "primary",
            "scope": "system",
            "owner_module": "openminion-llm",
            "labels": {"provider": provider_name or "unknown"},
        }
    if normalized.startswith("channels."):
        return {
            "component_kind": "channel_adapter",
            "component_id": default_channel or "default",
            "scope": "system",
            "owner_module": "openminion-controlplane",
            "labels": {"default_channel": default_channel or ""},
        }
    if normalized.startswith("plugins.") or normalized.startswith("skill."):
        return {
            "component_kind": "tool_runtime",
            "component_id": "plugin-registry",
            "scope": "system",
            "owner_module": "openminion-tool",
        }
    if normalized.startswith("security."):
        return {
            "component_kind": "runtime_manager",
            "component_id": "security-policy",
            "scope": "system",
            "owner_module": "openminion-policy",
        }
    if normalized.startswith("identity."):
        return {
            "component_kind": "agent_runtime",
            "component_id": "identity-profile",
            "scope": "agent",
            "owner_module": "openminion-identity",
        }
    return {
        "component_kind": "runtime_manager",
        "component_id": "primary",
        "scope": "system",
        "owner_module": "openminion-runtime",
    }


def _probe_related_ids_for_check_id(check_id: str) -> list[str] | None:
    normalized = str(check_id or "").strip().lower()
    if normalized in {
        "config.exists",
        "storage.ready",
        "provider.supported",
        "provider.cortensor.session_id",
        "channels.enabled",
        "channels.default_in_enabled",
        "plugins.enabled",
        "runtime.bootstrap",
    }:
        return [check_id]
    if normalized.startswith("provider.") and normalized.endswith(".key"):
        return [check_id]
    return None


def _emit_doctor_normalization_telemetry(
    *,
    checks: list[DoctorCheck],
    summary: dict[str, Any],
) -> None:
    _DOCTOR_LOG.info(
        "doctor.normalization.summary status=%s ok=%s fail=%d warn=%d checks=%d",
        summary.get("status", ""),
        bool(summary.get("ok", False)),
        int(summary.get("counts", {}).get("fail", 0)),
        int(summary.get("counts", {}).get("warn", 0)),
        len(checks),
    )
    fail_checks = [
        {"id": check.id, "finding_id": check.finding_id}
        for check in checks
        if check.status == "fail"
    ]
    if fail_checks:
        _DOCTOR_LOG.warning(
            "doctor.normalization.negative failed_findings=%s",
            fail_checks[:20],
        )


def _run_turn_smoke_check(
    app: APIRuntime,
    message: str,
    target: str,
    channel: str | None,
    agent_id: str | None,
) -> DoctorCheck:
    agent_profile = app.resolve_agent_profile(agent_id)
    agent_service = app.resolve_agent_service(agent_profile.name)
    selected_channel = (channel or agent_profile.default_channel).strip()
    started = perf_counter()
    try:
        app.channels.get(selected_channel)
        inbound = Message(channel=selected_channel, target=target, body=message)
        response = asyncio.run(agent_service.run_turn(inbound))
        latency_ms = int((perf_counter() - started) * 1000)
        if not response.text.strip():
            return DoctorCheck(
                id="agent.turn_smoke",
                status="fail",
                message="Turn smoke test returned empty response text",
                remediation="Inspect provider and plugin response mapping.",
            )
        return DoctorCheck(
            id="agent.turn_smoke",
            status="ok",
            message="Agent turn smoke test succeeded",
            details={
                "channel": response.channel,
                "target": response.target,
                "latency_ms": latency_ms,
                "response_chars": len(response.text),
                "agent": agent_profile.name,
                "provider": response.metadata.get("provider", agent_profile.provider),
                "model": response.metadata.get("model", ""),
            },
        )
    except Exception as exc:
        return DoctorCheck(
            id="agent.turn_smoke",
            status="fail",
            message=f"Agent turn smoke test failed: {exc}",
            remediation="Run `openminion agent-check --json` for detailed functional failure context.",
        )


def _doctor_check_from_security_finding(
    finding: SecurityValidateFinding,
) -> DoctorCheck:
    return DoctorCheck(
        id=f"security.{finding.id}",
        status=_status_from_severity(finding.severity),
        severity=finding.severity,
        message=finding.message,
        remediation=finding.remediation,
        details=finding.details,
    )


def _status_from_severity(severity: str) -> str:
    normalized = str(severity or "").strip().lower()
    if normalized == SEVERITY_CRITICAL:
        return "fail"
    if normalized == SEVERITY_WARN:
        return "warn"
    return "ok"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = subparsers.add_parser(
        "doctor", help="Run diagnostics and runtime status checks"
    )
    doctor.add_argument(
        "--check-turn",
        action="store_true",
        help="Run a real agent turn smoke check as part of diagnostics",
    )
    doctor.add_argument(
        "--message",
        default="doctor ping",
        help="Input message used with --check-turn (default: doctor ping)",
    )
    doctor.add_argument(
        "--target", default="doctor", help="Target used with --check-turn"
    )
    doctor.add_argument(
        "--channel",
        default=None,
        help="Channel used with --check-turn (default: selected agent default channel)",
    )
    doctor.add_argument(
        "--profile",
        "--agent-id",
        default=None,
        dest="agent_id",
        help="Configured profile id used with diagnostics (compat: --agent-id)",
    )
    doctor.add_argument(
        "--override-provider",
        default=None,
        help="Run-scoped provider override applied after profile selection",
    )
    doctor.add_argument(
        "--override-model",
        default=None,
        help="Run-scoped model override applied after profile selection",
    )
    doctor.add_argument(
        "--override-system-prompt",
        default=None,
        help="Run-scoped system prompt override applied after profile selection",
    )
    add_json_output_flag(doctor)
    doctor.set_defaults(handler=run_doctor, needs_app=False)
