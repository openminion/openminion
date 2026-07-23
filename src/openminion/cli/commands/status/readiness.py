from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.base.config import (
    ConfigError,
    ConfigManager,
    ConfigManagerError,
    OpenMinionConfig,
    resolve_config_path,
)
from openminion.cli.config import CLIRoots, resolve_cli_roots
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.bootstrap.onboarding import (
    OnboardingInspectionRequest,
    OnboardingStatus,
    OnboardingStatusService,
)


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    status: str
    message: str
    safe_next_action: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
        }
        if self.safe_next_action:
            payload["safe_next_action"] = self.safe_next_action
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def run_readiness_status(args: Any) -> int:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=roots.home_root,
    )
    onboarding = _inspect_onboarding(args, roots=roots, config_path=config_path)
    config = _load_config_for_readiness(config_path, roots.home_root, roots.data_root)
    checks = _build_readiness_checks(onboarding, config=config)
    payload = {
        "ok": True,
        "overall": _overall_status(checks),
        "config_path": str(config_path),
        "checks": [check.to_dict() for check in checks],
        "safe_next_actions": _safe_next_actions(checks),
    }
    _print_readiness_status(payload=payload, as_json=bool(getattr(args, "json", False)))
    return 0


def _inspect_onboarding(
    args: Any,
    *,
    roots: CLIRoots,
    config_path: Path,
) -> OnboardingStatus:
    return OnboardingStatusService().inspect(
        OnboardingInspectionRequest(
            config_path=config_path,
            home_root=roots.home_root,
            data_root=roots.data_root,
            config_arg=getattr(args, "config", None),
            agent_id=str(getattr(args, "agent_id", "") or "").strip() or None,
            has_tty=False,
            env=roots.env,
        )
    )


def _load_config_for_readiness(
    config_path: Path,
    home_root: Path,
    data_root: Path,
) -> OpenMinionConfig | None:
    try:
        return ConfigManager.load(
            str(config_path),
            home_root=home_root,
            data_root=data_root,
        ).base_config
    except (ConfigError, ConfigManagerError):
        return None


def _build_readiness_checks(
    onboarding: OnboardingStatus,
    *,
    config: OpenMinionConfig | None,
) -> list[ReadinessCheck]:
    return [
        _provider_check(onboarding),
        _module_family_check(
            "search_fetch",
            ("openminion.tools.search", "openminion.tools.fetch"),
            "Search and web fetch substrate is installed.",
        ),
        _module_family_check(
            "browser",
            ("openminion.tools.browser",),
            "Browser-control substrate is installed.",
            next_action="Run the browser smoke lane before claiming live web UI control.",
        ),
        _gws_check(),
        _channels_check(config),
        _module_family_check(
            "task_cron",
            ("openminion.tools.task", "openminion.cli.commands.cron"),
            "Task and cron substrate is installed.",
        ),
        _module_family_check(
            "memory",
            ("openminion.tools.memory",),
            "Memory write/search/correct/forget substrate is installed.",
        ),
        _policy_check(config),
    ]


def _provider_check(onboarding: OnboardingStatus) -> ReadinessCheck:
    status = "ready" if onboarding.can_continue else "blocked"
    action = "" if onboarding.can_continue else onboarding.remediation_command
    return ReadinessCheck(
        id="provider",
        status=status,
        message=onboarding.reason,
        safe_next_action=action,
        details={
            "state": onboarding.state.value,
            "track": onboarding.track.value,
            "provider_name": onboarding.provider_name,
            "credentials_ready": onboarding.credentials_ready,
        },
    )


def _module_family_check(
    check_id: str,
    module_names: tuple[str, ...],
    ready_message: str,
    *,
    next_action: str = "",
) -> ReadinessCheck:
    missing = [name for name in module_names if importlib.util.find_spec(name) is None]
    if missing:
        return ReadinessCheck(
            id=check_id,
            status="blocked",
            message=f"Missing Python module(s): {', '.join(missing)}.",
            safe_next_action="Install or enable the missing package modules.",
            details={"modules": list(module_names), "missing": missing},
        )
    return ReadinessCheck(
        id=check_id,
        status="available",
        message=ready_message,
        safe_next_action=next_action,
        details={"modules": list(module_names)},
    )


def _gws_check() -> ReadinessCheck:
    substrate = importlib.util.find_spec("openminion.tools.gws") is not None
    cli_path = shutil.which("gws")
    if substrate and cli_path:
        return ReadinessCheck(
            id="gws",
            status="available",
            message="Google Workspace substrate and `gws` CLI are available.",
            details={"cli_path": cli_path},
        )
    if substrate:
        return ReadinessCheck(
            id="gws",
            status="not_configured",
            message="Google Workspace substrate is installed; the external `gws` CLI is not on PATH.",
            safe_next_action="Run the Google Workspace onboarding/auth bootstrap before claiming live GWS use.",
        )
    return ReadinessCheck(
        id="gws",
        status="blocked",
        message="Google Workspace substrate is not installed.",
        safe_next_action="Install or enable the Google Workspace tool package.",
    )


def _channels_check(config: OpenMinionConfig | None) -> ReadinessCheck:
    if config is None:
        return ReadinessCheck(
            id="channels",
            status="blocked",
            message="Config did not load, so channel readiness cannot be resolved.",
            safe_next_action="Run `openminion setup` or fix the selected config.",
        )
    enabled = [str(channel) for channel in config.enabled_channels]
    if not enabled:
        return ReadinessCheck(
            id="channels",
            status="blocked",
            message="No enabled channels are configured.",
            safe_next_action="Set enabled_channels with at least `console`.",
        )
    return ReadinessCheck(
        id="channels",
        status="ready",
        message=f"Configured channel(s): {', '.join(enabled)}.",
        details={"enabled_channels": enabled},
    )


def _policy_check(config: OpenMinionConfig | None) -> ReadinessCheck:
    if config is None:
        return ReadinessCheck(
            id="policy",
            status="blocked",
            message="Config did not load, so action policy readiness cannot be resolved.",
            safe_next_action="Run `openminion setup` or fix the selected config.",
        )
    mode = str(config.action_policy.mode or "auto").strip() or "auto"
    default_action = (
        str(config.action_policy.default_action or "require_confirm").strip()
        or "require_confirm"
    )
    return ReadinessCheck(
        id="policy",
        status="ready",
        message=f"Action policy mode={mode}, default_action={default_action}.",
        details={"mode": mode, "default_action": default_action},
    )


def _overall_status(checks: list[ReadinessCheck]) -> str:
    statuses = {check.status for check in checks}
    if "blocked" in statuses:
        return "blocked"
    if "not_configured" in statuses:
        return "degraded"
    return "ready"


def _safe_next_actions(checks: list[ReadinessCheck]) -> list[dict[str, str]]:
    return [
        {"id": check.id, "action": check.safe_next_action}
        for check in checks
        if check.safe_next_action
    ]


def _print_readiness_status(*, payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return
    checks = payload["checks"]
    blocked = sum(1 for check in checks if check["status"] == "blocked")
    print(
        "status readiness: "
        f"overall={payload['overall']} checks={len(checks)} blocked={blocked}"
    )
    for check in checks:
        print(f"- {check['id']}: {check['status']} -- {check['message']}")
        if action := str(check.get("safe_next_action", "") or "").strip():
            print(f"  next: {action}")
