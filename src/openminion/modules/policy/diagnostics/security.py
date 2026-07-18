import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime.capability import resolve_plugin_runtime_policy
from openminion.modules.policy.runtime.security import is_local_gateway_host

SEVERITY_CRITICAL = "critical"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"


class PluginManifestView(Protocol):
    id: str
    trust_tier: str
    provenance_source: str
    provenance_verified: bool


@dataclass(frozen=True)
class SecurityValidateFinding:
    id: str
    severity: str
    message: str
    remediation: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class SecurityValidateReport:
    findings: list[SecurityValidateFinding]

    @property
    def critical_count(self) -> int:
        return sum(item.severity == SEVERITY_CRITICAL for item in self.findings)

    @property
    def warn_count(self) -> int:
        return sum(item.severity == SEVERITY_WARN for item in self.findings)

    @property
    def info_count(self) -> int:
        return sum(item.severity == SEVERITY_INFO for item in self.findings)

    @property
    def status(self) -> str:
        if self.critical_count:
            return "fail"
        if self.warn_count:
            return "warn"
        return "ok"


def run_security_validate(
    *,
    config: OpenMinionConfig,
    config_path: Path,
    storage_path: Path,
    memory_root: Path,
    loaded_plugin_manifest_ids: Sequence[str] | None = None,
    loaded_plugin_manifests: Sequence[PluginManifestView] | None = None,
    loaded_tool_names: Sequence[str] | None = None,
) -> SecurityValidateReport:
    findings: list[SecurityValidateFinding] = []
    external_gateway = _is_external_gateway_host(config.gateway.host)
    findings.append(
        _check_gateway_bind_posture(config=config, external_gateway=external_gateway)
    )
    findings.append(_check_gateway_auth_posture(external_gateway=external_gateway))
    findings.append(_check_auth_rate_limit_posture(external_gateway=external_gateway))
    findings.append(_check_origin_policy_posture(external_gateway=external_gateway))
    findings.extend(_check_channel_policy_posture(config=config))
    findings.append(_check_channel_authenticity_posture(config=config))
    findings.extend(
        _check_filesystem_permission_posture(
            config_path=config_path, storage_path=storage_path
        )
    )
    findings.append(_check_memory_retention_posture(config=config))
    findings.append(
        _permission_finding(
            check_id="filesystem.memory_permissions",
            path=memory_root,
            missing_message="Memory root does not exist yet; permission posture will be checked after first write.",
        )
    )
    findings.append(
        _check_plugin_trust_posture(
            config=config,
            loaded_plugin_manifest_ids=loaded_plugin_manifest_ids or [],
            loaded_plugin_manifests=loaded_plugin_manifests or [],
        )
    )
    findings.append(_check_secret_redaction_posture(config=config))
    findings.append(
        _check_untrusted_content_boundary_posture(
            config=config,
            loaded_tool_names=loaded_tool_names or [],
        )
    )
    findings.append(_check_execution_boundary_posture(config=config))

    return SecurityValidateReport(findings=findings)


def _check_gateway_bind_posture(
    *, config: OpenMinionConfig, external_gateway: bool
) -> SecurityValidateFinding:
    host = (config.gateway.host or "").strip() or "127.0.0.1"
    if external_gateway:
        return SecurityValidateFinding(
            id="gateway.bind_posture",
            severity=SEVERITY_WARN,
            message=f"Gateway host is externally reachable (`{host}`).",
            remediation="Use localhost bind for local development or enforce auth and network controls.",
            details={"host": host, "port": config.gateway.port},
        )
    return SecurityValidateFinding(
        id="gateway.bind_posture",
        severity=SEVERITY_INFO,
        message=f"Gateway host `{host}` is local-only.",
        details={"host": host, "port": config.gateway.port},
    )


def _check_gateway_auth_posture(*, external_gateway: bool) -> SecurityValidateFinding:
    if external_gateway:
        return SecurityValidateFinding(
            id="gateway.auth_posture",
            severity=SEVERITY_WARN,
            message=(
                "Gateway auth posture is not explicitly configurable yet while external bind is enabled."
            ),
            remediation=(
                "Place gateway behind authenticated ingress and enable method-level authz controls."
            ),
        )
    return SecurityValidateFinding(
        id="gateway.auth_posture",
        severity=SEVERITY_INFO,
        message="Gateway auth posture relies on local-only bind + protocol authz baseline.",
    )


def _check_auth_rate_limit_posture(
    *, external_gateway: bool
) -> SecurityValidateFinding:
    if external_gateway:
        return SecurityValidateFinding(
            id="gateway.auth_rate_limit_posture",
            severity=SEVERITY_WARN,
            message="Auth rate-limit policy is not explicitly configured for external exposure.",
            remediation="Add ingress-level rate limiting until core auth-rate policy settings are exposed.",
        )
    return SecurityValidateFinding(
        id="gateway.auth_rate_limit_posture",
        severity=SEVERITY_INFO,
        message="Auth rate-limit risk is low for local-only gateway bind.",
    )


def _check_origin_policy_posture(*, external_gateway: bool) -> SecurityValidateFinding:
    if external_gateway:
        return SecurityValidateFinding(
            id="gateway.origin_policy_posture",
            severity=SEVERITY_WARN,
            message="Origin allowlist policy is not explicitly configured for external exposure.",
            remediation="Use a reverse proxy/origin allowlist in front of externally reachable control-plane APIs.",
        )
    return SecurityValidateFinding(
        id="gateway.origin_policy_posture",
        severity=SEVERITY_INFO,
        message="Origin policy risk is low for local-only gateway bind.",
    )


def _check_channel_policy_posture(
    *, config: OpenMinionConfig
) -> list[SecurityValidateFinding]:
    findings: list[SecurityValidateFinding] = []

    dm_policy = (config.channel_policy.dm_policy or "").strip().lower()
    if dm_policy == "open":
        findings.append(
            SecurityValidateFinding(
                id="channel.dm_policy",
                severity=SEVERITY_WARN,
                message="DM policy is `open`; unknown senders are allowed.",
                remediation="Prefer `pairing` or `allowlist` for safer inbound defaults.",
            )
        )
    else:
        findings.append(
            SecurityValidateFinding(
                id="channel.dm_policy",
                severity=SEVERITY_INFO,
                message=f"DM policy is `{dm_policy or 'pairing'}`.",
            )
        )

    group_policy = (config.channel_policy.group_policy or "").strip().lower()
    if group_policy == "open":
        findings.append(
            SecurityValidateFinding(
                id="channel.group_policy",
                severity=SEVERITY_WARN,
                message="Group policy is `open`; unrestricted group inbound is enabled.",
                remediation="Prefer `allowlist` or `disabled` for safer defaults.",
            )
        )
    else:
        findings.append(
            SecurityValidateFinding(
                id="channel.group_policy",
                severity=SEVERITY_INFO,
                message=f"Group policy is `{group_policy or 'disabled'}`.",
            )
        )

    return findings


def _check_filesystem_permission_posture(
    *, config_path: Path, storage_path: Path
) -> list[SecurityValidateFinding]:
    findings: list[SecurityValidateFinding] = []
    findings.append(
        _permission_finding(
            check_id="filesystem.config_permissions",
            path=config_path,
            missing_message="Config file does not exist yet; permission posture will be checked after creation.",
        )
    )
    findings.append(
        _permission_finding(
            check_id="filesystem.storage_permissions",
            path=storage_path.parent,
            missing_message="Storage directory does not exist yet; permission posture will be checked after creation.",
        )
    )
    return findings


def _check_channel_authenticity_posture(
    *, config: OpenMinionConfig
) -> SecurityValidateFinding:
    mode = str(config.channel_authenticity.mode or "").strip().lower() or "warn"
    non_console_channels = sorted(
        {
            str(item).strip().lower()
            for item in config.enabled_channels
            if str(item).strip() and str(item).strip().lower() != "console"
        }
    )
    required_channels = sorted(
        {
            str(item).strip().lower()
            for item in config.channel_authenticity.required_channels
            if str(item).strip()
        }
    )
    secret_env_by_channel = {
        str(key).strip().lower(): str(value).strip()
        for key, value in config.channel_authenticity.secret_env_by_channel.items()
        if str(key).strip() and str(value).strip()
    }

    if not non_console_channels:
        return SecurityValidateFinding(
            id="channel.authenticity_policy",
            severity=SEVERITY_INFO,
            message=f"Inbound authenticity policy mode is `{mode}` (no external channels enabled).",
            details={"mode": mode},
        )

    if mode == "off":
        return SecurityValidateFinding(
            id="channel.authenticity_policy",
            severity=SEVERITY_WARN,
            message="Inbound authenticity policy is disabled while external channels are enabled.",
            remediation="Use `warn` or `require` mode and configure channel signature secrets.",
            details={"mode": mode, "channels": non_console_channels},
        )

    required_targets = sorted(set(required_channels or non_console_channels))
    missing_secret_env = sorted(
        channel for channel in required_targets if channel not in secret_env_by_channel
    )
    if mode == "require" and missing_secret_env:
        return SecurityValidateFinding(
            id="channel.authenticity_policy",
            severity=SEVERITY_WARN,
            message="Require-mode authenticity policy has channels without signature secret env mapping.",
            remediation="Populate `channel_authenticity.secret_env_by_channel` for required channels.",
            details={
                "mode": mode,
                "required_channels": required_targets,
                "missing_secret_env_channels": missing_secret_env,
            },
        )

    severity = SEVERITY_WARN if mode == "warn" else SEVERITY_INFO
    message = (
        "Inbound authenticity policy is in warn mode for external channels."
        if mode == "warn"
        else "Inbound authenticity policy is enforced for external channels."
    )
    remediation = (
        "Move to `require` mode after validating signatures in production."
        if mode == "warn"
        else ""
    )
    return SecurityValidateFinding(
        id="channel.authenticity_policy",
        severity=severity,
        message=message,
        remediation=remediation,
        details={
            "mode": mode,
            "channels": non_console_channels,
            "required_channels": required_targets,
        },
    )


def _check_memory_retention_posture(
    *, config: OpenMinionConfig
) -> SecurityValidateFinding:
    if not bool(config.runtime.memory_enabled):
        return SecurityValidateFinding(
            id="memory.retention_posture",
            severity=SEVERITY_INFO,
            message="Canonical memory runtime is disabled.",
            details={"memory_enabled": False},
        )

    retention_days = int(config.runtime.memory_log_retention_days)
    if retention_days > 3650:
        return SecurityValidateFinding(
            id="memory.retention_posture",
            severity=SEVERITY_WARN,
            message="Memory log retention is very high.",
            remediation="Use a bounded retention window to control disk growth (for example 30-365 days).",
            details={"memory_log_retention_days": retention_days},
        )

    summary_chars = int(config.runtime.session_summary_max_chars)
    if summary_chars > 20000:
        return SecurityValidateFinding(
            id="memory.retention_posture",
            severity=SEVERITY_WARN,
            message="Session summary char budget is very high.",
            remediation="Use a tighter summary budget to reduce prompt bloat risk.",
            details={"session_summary_max_chars": summary_chars},
        )

    return SecurityValidateFinding(
        id="memory.retention_posture",
        severity=SEVERITY_INFO,
        message="Memory retention and compaction posture is bounded.",
        details={
            "memory_enabled": True,
            "memory_log_retention_days": retention_days,
            "session_summary_max_chars": summary_chars,
        },
    )


def _permission_finding(
    *, check_id: str, path: Path, missing_message: str
) -> SecurityValidateFinding:
    if not path.exists():
        return SecurityValidateFinding(
            id=check_id,
            severity=SEVERITY_INFO,
            message=missing_message,
            details={"path": str(path)},
        )

    mode = path.stat().st_mode
    is_world_writable = bool(mode & stat.S_IWOTH)
    details = {"path": str(path), "mode_octal": oct(stat.S_IMODE(mode))}

    if is_world_writable:
        return SecurityValidateFinding(
            id=check_id,
            severity=SEVERITY_WARN,
            message=f"Path is world-writable: {path}",
            remediation="Restrict permissions (for example chmod 750 on dirs, 640 on files).",
            details=details,
        )
    return SecurityValidateFinding(
        id=check_id,
        severity=SEVERITY_INFO,
        message=f"Path permissions are not world-writable: {path}",
        details=details,
    )


def _check_plugin_trust_posture(
    *,
    config: OpenMinionConfig,
    loaded_plugin_manifest_ids: Sequence[str],
    loaded_plugin_manifests: Sequence[PluginManifestView],
) -> SecurityValidateFinding:
    manifests = list(loaded_plugin_manifests)
    if manifests:
        local_dev_plugins = sorted(
            manifest.id for manifest in manifests if manifest.trust_tier == "local-dev"
        )
        restricted_unverified = sorted(
            manifest.id
            for manifest in manifests
            if manifest.trust_tier == "restricted" and not manifest.provenance_verified
        )
        verified_local_unverified = sorted(
            manifest.id
            for manifest in manifests
            if manifest.trust_tier == "verified"
            and manifest.provenance_source == "local-path"
            and not manifest.provenance_verified
        )
        if restricted_unverified:
            return SecurityValidateFinding(
                id="plugins.trust_posture",
                severity=SEVERITY_CRITICAL,
                message="Restricted-tier plugins are enabled without verified provenance.",
                remediation="Only activate restricted plugins with verified provenance metadata.",
                details={"plugin_ids": restricted_unverified},
            )
        if verified_local_unverified:
            return SecurityValidateFinding(
                id="plugins.trust_posture",
                severity=SEVERITY_WARN,
                message="Verified-tier plugins rely on local unverified provenance.",
                remediation="Use verified provenance (`registry|git|package` or verified local attestations).",
                details={"plugin_ids": verified_local_unverified},
            )
        if local_dev_plugins:
            return SecurityValidateFinding(
                id="plugins.trust_posture",
                severity=SEVERITY_WARN,
                message="Local-dev trust-tier plugins are enabled.",
                remediation="Use `verified` or `restricted` tiers for production plugin posture.",
                details={"plugin_ids": local_dev_plugins},
            )
        return SecurityValidateFinding(
            id="plugins.trust_posture",
            severity=SEVERITY_INFO,
            message="Plugin trust tiers and provenance posture are acceptable.",
            details={
                "plugin_manifest_ids": sorted(manifest.id for manifest in manifests)
            },
        )

    manifest_ids = [
        str(item).strip() for item in loaded_plugin_manifest_ids if str(item).strip()
    ]
    if not manifest_ids:
        enabled_plugins = list(
            resolve_plugin_runtime_policy(
                compatibility_enabled_plugins=list(config.enabled_plugins),
                system_policy=getattr(config.runtime, "plugins", None),
            ).effective_enabled
        )
        if not enabled_plugins:
            return SecurityValidateFinding(
                id="plugins.trust_posture",
                severity=SEVERITY_WARN,
                message="No plugins are enabled; trust/capability posture cannot be evaluated.",
                remediation="Enable baseline plugins and re-run security validation.",
            )
        manifest_ids = enabled_plugins

    non_builtin = [item for item in manifest_ids if not item.startswith("builtin.")]
    if non_builtin:
        return SecurityValidateFinding(
            id="plugins.trust_posture",
            severity=SEVERITY_WARN,
            message="Non-built-in plugins are enabled without explicit trust-tier enforcement.",
            remediation="Review plugin provenance and requested capabilities before production rollout.",
            details={"non_builtin_plugins": sorted(non_builtin)},
        )
    return SecurityValidateFinding(
        id="plugins.trust_posture",
        severity=SEVERITY_INFO,
        message="Enabled plugins are built-in only.",
        details={"plugin_manifest_ids": sorted(manifest_ids)},
    )


def _check_secret_redaction_posture(
    *, config: OpenMinionConfig
) -> SecurityValidateFinding:
    inline_secret_fields = _inline_secret_fields(config=config)
    if inline_secret_fields:
        return SecurityValidateFinding(
            id="secrets.redaction_posture",
            severity=SEVERITY_WARN,
            message="Inline API secrets were found in config.",
            remediation="Prefer environment variables and keep config secret-free.",
            details={"inline_secret_fields": inline_secret_fields},
        )
    return SecurityValidateFinding(
        id="secrets.redaction_posture",
        severity=SEVERITY_INFO,
        message="No inline provider API secrets detected in config.",
    )


def _check_untrusted_content_boundary_posture(
    *,
    config: OpenMinionConfig,
    loaded_tool_names: Sequence[str],
) -> SecurityValidateFinding:
    channels = [
        str(item).strip().lower()
        for item in config.enabled_channels
        if str(item).strip()
    ]
    tool_names = [
        str(item).strip().lower() for item in loaded_tool_names if str(item).strip()
    ]
    risky_tools = [
        item
        for item in tool_names
        if any(
            token in item
            for token in (
                "browser",
                "http_request",
                "api_spec_call",
            )
        )
    ]
    non_console_channels = [item for item in channels if item != "console"]

    if non_console_channels or risky_tools:
        return SecurityValidateFinding(
            id="untrusted_content.boundary_posture",
            severity=SEVERITY_WARN,
            message="External content surfaces are enabled and require explicit boundary wrappers.",
            remediation="Ensure untrusted-content wrappers are applied before model/tool execution.",
            details={
                "channels": sorted(non_console_channels),
                "risky_tools": sorted(risky_tools),
            },
        )
    return SecurityValidateFinding(
        id="untrusted_content.boundary_posture",
        severity=SEVERITY_INFO,
        message="No external-content-heavy channels/tools detected in current runtime config.",
    )


def _inline_secret_fields(*, config: OpenMinionConfig) -> list[str]:
    fields: list[tuple[str, str]] = [
        ("providers.openai.api_key", config.providers.openai.api_key),
        ("providers.anthropic.api_key", config.providers.anthropic.api_key),
        ("providers.openrouter.api_key", config.providers.openrouter.api_key),
        ("providers.ollama.api_key", config.providers.ollama.api_key),
        ("providers.cortensor.api_key", config.providers.cortensor.api_key),
    ]
    return [path for path, value in fields if str(value).strip()]


def _check_execution_boundary_posture(
    *,
    config: OpenMinionConfig,
) -> SecurityValidateFinding:
    return SecurityValidateFinding(
        id="execution.boundary.policy",
        severity=SEVERITY_INFO,
        message="Execution-boundary policy adapter is enforced for tool execution.",
    )


def _is_external_gateway_host(host: str) -> bool:
    return not is_local_gateway_host(host)
