import argparse
from dataclasses import dataclass
from difflib import get_close_matches
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from openminion.base.config import (
    ConfigError,
    ConfigManager,
    ConfigManagerError,
    OpenMinionConfig,
    UnknownProfileError,
    resolve_agent_config,
)
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.config.env.schema import EnvValidationResult, validate_for_provider
from openminion.modules.llm.providers.factory import SUPPORTED_PROVIDERS
from openminion.services.health.probes import (
    ProbeResult,
    StorageProbeResult,
    probe_provider_session,
    probe_provider_supported,
    probe_storage_ready,
)


DEFAULT_SETUP_REMEDIATION = "openminion setup"


class OnboardingRequestedMode(StrEnum):
    AUTO = "auto"
    DEMO = "demo"


class OnboardingTrack(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"
    DEMO = "demo"
    UNKNOWN = "unknown"


class OnboardingState(StrEnum):
    READY = "ready"
    MISSING_CONFIG = "missing_config"
    INCOMPLETE_CLOUD_CREDENTIALS = "incomplete_cloud_credentials"
    LOCAL_OLLAMA = "local_ollama"
    EXPLICIT_DEMO = "explicit_demo"
    CONFIG_ERROR = "config_error"


class OnboardingAction(StrEnum):
    CONTINUE = "continue"
    LAUNCH_SETUP = "launch_setup"
    FAIL_FAST = "fail_fast"


class OnboardingPlanStep(StrEnum):
    BYPASS_ONBOARDING = "bypass_onboarding"
    LAUNCH_SETUP = "launch_setup"
    SELECT_TRACK = "select_track"
    SELECT_PROVIDER = "select_provider"
    COLLECT_CREDENTIALS = "collect_credentials"
    VALIDATE_LOCAL_RUNTIME = "validate_local_runtime"
    RUN_DOCTOR = "run_doctor"
    ENTER_CHAT = "enter_chat"
    FAIL_WITH_REMEDIATION = "fail_with_remediation"


@dataclass(frozen=True)
class OnboardingInspectionRequest:
    config_path: Path
    home_root: Path
    data_root: Path
    config_arg: str | None = None
    agent_id: str | None = None
    requested_mode: OnboardingRequestedMode = OnboardingRequestedMode.AUTO
    has_tty: bool = True
    env: EnvironmentConfig | Mapping[str, object] | None = None
    runtime_env: Mapping[str, object] | None = None
    process_env: Mapping[str, object] | None = None
    remediation_command: str = DEFAULT_SETUP_REMEDIATION


@dataclass(frozen=True)
class OnboardingStatus:
    state: OnboardingState
    action: OnboardingAction
    track: OnboardingTrack
    reason: str
    config_path: Path
    home_root: Path
    data_root: Path
    provider_name: str = ""
    config_exists: bool = False
    storage_ready: bool = False
    provider_supported: bool = False
    credentials_ready: bool = False
    has_tty: bool = True
    required_env_vars: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    remediation_command: str = DEFAULT_SETUP_REMEDIATION

    @property
    def can_continue(self) -> bool:
        return self.action == OnboardingAction.CONTINUE


@dataclass(frozen=True)
class OnboardingPlan:
    status: OnboardingStatus
    steps: tuple[OnboardingPlanStep, ...]
    interactive: bool
    summary: str
    remediation_command: str = DEFAULT_SETUP_REMEDIATION

    @property
    def launches_setup(self) -> bool:
        return OnboardingPlanStep.LAUNCH_SETUP in self.steps


@dataclass(frozen=True)
class OnboardingSurfaceRoute:
    status: OnboardingStatus
    config_path: Path
    home_root: Path
    data_root: Path

    @property
    def should_continue(self) -> bool:
        return self.status.action == OnboardingAction.CONTINUE

    @property
    def should_launch_setup(self) -> bool:
        return self.status.action == OnboardingAction.LAUNCH_SETUP

    @property
    def should_fail_fast(self) -> bool:
        return self.status.action == OnboardingAction.FAIL_FAST


class OnboardingStatusService:
    """Classify onboarding readiness without embedding UI-specific behavior."""

    def inspect(self, request: OnboardingInspectionRequest) -> OnboardingStatus:
        if request.requested_mode == OnboardingRequestedMode.DEMO:
            return self._explicit_demo_status(
                request, reason="Explicit demo mode requested."
            )
        if not request.config_path.exists():
            return self._missing_config_status(request)
        loaded = self._load_config_for_inspection(request)
        if isinstance(loaded, OnboardingStatus):
            return loaded
        agent_config = self._resolve_agent_for_inspection(request, loaded)
        if isinstance(agent_config, OnboardingStatus):
            return agent_config
        return self._inspect_loaded_config(
            request=request, config=loaded, agent_config=agent_config
        )

    def _explicit_demo_status(
        self,
        request: OnboardingInspectionRequest,
        *,
        reason: str,
        provider_name: str = "",
    ) -> OnboardingStatus:
        return OnboardingStatus(
            state=OnboardingState.EXPLICIT_DEMO,
            action=OnboardingAction.CONTINUE,
            track=OnboardingTrack.DEMO,
            reason=reason,
            config_path=request.config_path,
            home_root=request.home_root,
            data_root=request.data_root,
            provider_name=provider_name,
            config_exists=bool(provider_name),
            storage_ready=bool(provider_name),
            provider_supported=bool(provider_name),
            credentials_ready=bool(provider_name),
            has_tty=request.has_tty,
            remediation_command=request.remediation_command,
        )

    def _missing_config_status(
        self, request: OnboardingInspectionRequest
    ) -> OnboardingStatus:
        explicit_config = _has_explicit_config_arg(request)
        return OnboardingStatus(
            state=OnboardingState.MISSING_CONFIG,
            action=self._missing_config_action(
                request.has_tty, explicit_config=explicit_config
            ),
            track=OnboardingTrack.UNKNOWN,
            reason=_format_missing_config_reason(request),
            config_path=request.config_path,
            home_root=request.home_root,
            data_root=request.data_root,
            has_tty=request.has_tty,
            remediation_command="" if explicit_config else request.remediation_command,
        )

    def _load_config_for_inspection(
        self, request: OnboardingInspectionRequest
    ) -> OpenMinionConfig | OnboardingStatus:
        try:
            return ConfigManager.load(
                str(request.config_path),
                home_root=request.home_root,
                data_root=request.data_root,
            ).base_config
        except (ConfigError, ConfigManagerError) as exc:
            return self._config_error_status(
                request, reason=str(exc), issues=(str(exc),)
            )

    def _resolve_agent_for_inspection(
        self,
        request: OnboardingInspectionRequest,
        config: OpenMinionConfig,
    ) -> object | OnboardingStatus:
        try:
            return resolve_agent_config(config, request.agent_id)
        except UnknownProfileError as exc:
            return self._config_error_status(
                request,
                reason=str(exc),
                issues=(str(exc),),
                action=OnboardingAction.FAIL_FAST,
            )

    def _inspect_loaded_config(
        self,
        *,
        request: OnboardingInspectionRequest,
        config: OpenMinionConfig,
        agent_config: object,
    ) -> OnboardingStatus:
        provider_name = _normalize_provider_name(getattr(agent_config, "provider", ""))
        provider_track = _classify_track(provider_name)
        if provider_name == "echo":
            return self._echo_provider_status(
                request=request, config=config, provider_name=provider_name
            )
        env = _resolve_onboarding_environment(request=request, config=config)
        storage_result = probe_storage_ready(Path(config.storage.path).expanduser())
        supported_probe = probe_provider_supported(
            provider_name=provider_name, supported_providers=SUPPORTED_PROVIDERS
        )
        provider_validation = validate_for_provider(
            provider_name=provider_name, env=env, config=config
        )
        provider_session = probe_provider_session(config, provider_name)
        issues = _onboarding_probe_issues(
            storage_result, supported_probe, provider_session
        )
        if provider_track == OnboardingTrack.LOCAL and provider_name == "ollama":
            return self._local_ollama_status(
                request,
                provider_name,
                provider_track,
                storage_result,
                supported_probe,
                issues,
            )
        if not provider_validation.ok:
            issues.extend(provider_validation.errors)
            return self._credential_error_status(
                request,
                provider_name,
                provider_track,
                storage_result,
                supported_probe,
                provider_validation,
                issues,
            )
        if issues:
            return self._config_error_status(
                request,
                reason=issues[0],
                issues=tuple(issues),
                provider_name=provider_name,
                track=provider_track,
                storage_ready=storage_result.probe.status == "ok",
                provider_supported=supported_probe.status == "ok",
                credentials_ready=True,
            )
        return self._ready_status(
            request, provider_name, provider_track, provider_validation.required_vars
        )

    def _echo_provider_status(
        self,
        *,
        request: OnboardingInspectionRequest,
        config: OpenMinionConfig,
        provider_name: str,
    ) -> OnboardingStatus:
        if bool(getattr(getattr(config, "runtime", None), "demo_mode", False)):
            return self._explicit_demo_status(
                request,
                reason="Config is explicitly marked as demo mode.",
                provider_name=provider_name,
            )
        reason = (
            "Echo provider is demo-only. Use `openminion --demo` or rerun "
            "`openminion setup` to pick a real provider."
        )
        return self._config_error_status(
            request,
            reason=reason,
            issues=(reason,),
            provider_name=provider_name,
            track=OnboardingTrack.DEMO,
            storage_ready=True,
            provider_supported=True,
            credentials_ready=True,
        )

    def _config_error_status(
        self,
        request: OnboardingInspectionRequest,
        *,
        reason: str,
        issues: tuple[str, ...],
        action: OnboardingAction | None = None,
        provider_name: str = "",
        track: OnboardingTrack = OnboardingTrack.UNKNOWN,
        storage_ready: bool = False,
        provider_supported: bool = False,
        credentials_ready: bool = False,
    ) -> OnboardingStatus:
        return OnboardingStatus(
            state=OnboardingState.CONFIG_ERROR,
            action=action or self._missing_config_action(request.has_tty),
            track=track,
            reason=reason,
            config_path=request.config_path,
            home_root=request.home_root,
            data_root=request.data_root,
            provider_name=provider_name,
            config_exists=bool(provider_name),
            storage_ready=storage_ready,
            provider_supported=provider_supported,
            credentials_ready=credentials_ready,
            has_tty=request.has_tty,
            issues=issues,
            remediation_command=request.remediation_command,
        )

    def _local_ollama_status(
        self,
        request: OnboardingInspectionRequest,
        provider_name: str,
        provider_track: OnboardingTrack,
        storage_result: StorageProbeResult,
        supported_probe: ProbeResult,
        issues: list[str],
    ) -> OnboardingStatus:
        storage_ready = storage_result.probe.status == "ok"
        provider_supported = supported_probe.status == "ok"
        return OnboardingStatus(
            state=OnboardingState.LOCAL_OLLAMA,
            action=OnboardingAction.CONTINUE
            if storage_ready and provider_supported
            else self._missing_config_action(request.has_tty),
            track=provider_track,
            reason="Ollama provider selected; local onboarding path applies.",
            config_path=request.config_path,
            home_root=request.home_root,
            data_root=request.data_root,
            provider_name=provider_name,
            config_exists=True,
            storage_ready=storage_ready,
            provider_supported=provider_supported,
            credentials_ready=True,
            has_tty=request.has_tty,
            issues=tuple(issues),
            remediation_command=request.remediation_command,
        )

    def _credential_error_status(
        self,
        request: OnboardingInspectionRequest,
        provider_name: str,
        provider_track: OnboardingTrack,
        storage_result: StorageProbeResult,
        supported_probe: ProbeResult,
        provider_validation: EnvValidationResult,
        issues: list[str],
    ) -> OnboardingStatus:
        return OnboardingStatus(
            state=OnboardingState.INCOMPLETE_CLOUD_CREDENTIALS,
            action=self._missing_config_action(request.has_tty),
            track=provider_track,
            reason=provider_validation.errors[0],
            config_path=request.config_path,
            home_root=request.home_root,
            data_root=request.data_root,
            provider_name=provider_name,
            config_exists=True,
            storage_ready=storage_result.probe.status == "ok",
            provider_supported=supported_probe.status == "ok",
            credentials_ready=False,
            has_tty=request.has_tty,
            required_env_vars=provider_validation.required_vars,
            issues=tuple(issues),
            remediation_command=request.remediation_command,
        )

    def _ready_status(
        self,
        request: OnboardingInspectionRequest,
        provider_name: str,
        provider_track: OnboardingTrack,
        required_env_vars: tuple[str, ...],
    ) -> OnboardingStatus:
        return OnboardingStatus(
            state=OnboardingState.READY,
            action=OnboardingAction.CONTINUE,
            track=provider_track,
            reason="Config, provider, and storage are ready for onboarding bypass.",
            config_path=request.config_path,
            home_root=request.home_root,
            data_root=request.data_root,
            provider_name=provider_name,
            config_exists=True,
            storage_ready=True,
            provider_supported=True,
            credentials_ready=True,
            has_tty=request.has_tty,
            required_env_vars=required_env_vars,
            remediation_command=request.remediation_command,
        )

    def build_plan(self, status: OnboardingStatus) -> OnboardingPlan:
        if status.action == OnboardingAction.CONTINUE:
            if status.state == OnboardingState.EXPLICIT_DEMO:
                return OnboardingPlan(
                    status=status,
                    steps=(
                        OnboardingPlanStep.BYPASS_ONBOARDING,
                        OnboardingPlanStep.ENTER_CHAT,
                    ),
                    interactive=status.has_tty,
                    summary="Explicit demo mode selected; continue without setup.",
                    remediation_command=status.remediation_command,
                )
            if status.track == OnboardingTrack.LOCAL:
                return OnboardingPlan(
                    status=status,
                    steps=(
                        OnboardingPlanStep.VALIDATE_LOCAL_RUNTIME,
                        OnboardingPlanStep.ENTER_CHAT,
                    ),
                    interactive=status.has_tty,
                    summary="Local runtime is selected and ready to continue.",
                    remediation_command=status.remediation_command,
                )
            return OnboardingPlan(
                status=status,
                steps=(
                    OnboardingPlanStep.BYPASS_ONBOARDING,
                    OnboardingPlanStep.ENTER_CHAT,
                ),
                interactive=status.has_tty,
                summary="Onboarding is complete; continue into chat.",
                remediation_command=status.remediation_command,
            )

        if status.action == OnboardingAction.FAIL_FAST:
            return OnboardingPlan(
                status=status,
                steps=(OnboardingPlanStep.FAIL_WITH_REMEDIATION,),
                interactive=False,
                summary=(
                    "Interactive setup is unavailable; fail fast with exact "
                    "remediation."
                ),
                remediation_command=status.remediation_command,
            )

        steps = [
            OnboardingPlanStep.LAUNCH_SETUP,
            OnboardingPlanStep.SELECT_TRACK,
        ]
        if status.track != OnboardingTrack.LOCAL:
            steps.append(OnboardingPlanStep.SELECT_PROVIDER)
        if status.state == OnboardingState.INCOMPLETE_CLOUD_CREDENTIALS:
            steps.append(OnboardingPlanStep.COLLECT_CREDENTIALS)
        if status.track == OnboardingTrack.LOCAL:
            steps.append(OnboardingPlanStep.VALIDATE_LOCAL_RUNTIME)
        steps.append(OnboardingPlanStep.RUN_DOCTOR)
        steps.append(OnboardingPlanStep.ENTER_CHAT)
        return OnboardingPlan(
            status=status,
            steps=tuple(steps),
            interactive=True,
            summary="Launch deterministic setup, validate with doctor, then continue.",
            remediation_command=status.remediation_command,
        )

    @staticmethod
    def _missing_config_action(
        has_tty: bool, *, explicit_config: bool = False
    ) -> OnboardingAction:
        if explicit_config:
            return OnboardingAction.FAIL_FAST
        return OnboardingAction.LAUNCH_SETUP if has_tty else OnboardingAction.FAIL_FAST


def _normalize_provider_name(provider_name: str | None) -> str:
    normalized = str(provider_name or "").strip().lower() or "echo"
    if normalized == "claude":
        return "anthropic"
    return normalized


def _classify_track(provider_name: str) -> OnboardingTrack:
    if provider_name == "echo":
        return OnboardingTrack.DEMO
    if provider_name == "ollama":
        return OnboardingTrack.LOCAL
    if provider_name:
        return OnboardingTrack.CLOUD
    return OnboardingTrack.UNKNOWN


def _onboarding_probe_issues(
    storage_result: StorageProbeResult,
    supported_probe: ProbeResult,
    provider_session: ProbeResult | None,
) -> list[str]:
    issues: list[str] = []
    if supported_probe.status != "ok":
        issues.append(supported_probe.message)
    if storage_result.probe.status != "ok":
        issues.append(storage_result.probe.message)
    if provider_session is not None and provider_session.status != "ok":
        issues.append(provider_session.message)
    return issues


def _resolve_onboarding_environment(
    *, request: OnboardingInspectionRequest, config: OpenMinionConfig
) -> EnvironmentConfig:
    config_runtime_env = dict(
        getattr(getattr(config, "runtime", None), "env", {}) or {}
    )
    requested_runtime_env = dict(request.runtime_env or {})
    merged_runtime_env = dict(config_runtime_env)
    merged_runtime_env.update(requested_runtime_env)

    if request.process_env is not None:
        effective_process_env = request.process_env
    elif isinstance(request.env, EnvironmentConfig):
        effective_process_env = request.env.snapshot()
    elif isinstance(request.env, Mapping):
        effective_process_env = request.env
    else:
        effective_process_env = None

    return resolve_environment_config(
        runtime_env=merged_runtime_env,
        process_env=effective_process_env,
    )


def _has_explicit_config_arg(request: OnboardingInspectionRequest) -> bool:
    return bool(str(request.config_arg or "").strip())


def _format_missing_config_reason(request: OnboardingInspectionRequest) -> str:
    resolved_path = request.config_path
    config_arg = str(request.config_arg or "").strip()
    home_root = request.home_root
    suggestion_suffix = _format_missing_config_suggestions(resolved_path)
    if not config_arg:
        return (
            f"Default config file not found at {resolved_path}. "
            f"Omitted `--config` resolves under the effective home root {home_root} "
            f"using `{resolved_path.parent.name}/{resolved_path.name}`."
            f"{_format_legacy_default_config_hint(resolved_path)}"
        )

    raw_path = Path(config_arg)
    if raw_path.is_absolute():
        return (
            f"Config file does not exist: {resolved_path}. "
            "The value came from the explicit `--config` argument."
            f"{suggestion_suffix}"
        )

    return (
        f"Config file does not exist: {resolved_path}. "
        f"The explicit `--config {config_arg}` value was resolved relative to the "
        "current working directory, not `OPENMINION_HOME`/`--home-root`."
        f"{suggestion_suffix}"
    )


def _format_legacy_default_config_hint(resolved_path: Path) -> str:
    if resolved_path.name != "agents.json":
        return ""
    legacy_path = resolved_path.with_name("agent.json")
    if not legacy_path.exists():
        return ""
    return (
        f" Legacy default config detected at {legacy_path}. "
        "Rename it to `agents"
        ".json` or pass it explicitly with `--config`."
    )


def _format_missing_config_suggestions(resolved_path: Path) -> str:
    parent = resolved_path.parent
    if not parent.is_dir():
        return ""
    try:
        candidates = sorted(
            entry.name
            for entry in parent.iterdir()
            if entry.is_file()
            and (resolved_path.suffix == "" or entry.suffix == resolved_path.suffix)
        )
    except OSError:
        return ""
    if not candidates:
        return ""
    matches = get_close_matches(resolved_path.name, candidates, n=3, cutoff=0.55)
    if not matches:
        return ""
    rendered = [str((parent / name).resolve(strict=False)) for name in matches]
    if len(rendered) == 1:
        return f" Did you mean {rendered[0]}?"
    return " Did you mean one of: " + ", ".join(rendered) + "?"


def resolve_surface_onboarding_route(
    *,
    config_path: Path,
    home_root: Path,
    data_root: Path,
    config_arg: str | None = None,
    agent_id: str | None = None,
    requested_mode: OnboardingRequestedMode = OnboardingRequestedMode.AUTO,
    has_tty: bool = True,
    no_interactive: bool = False,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    remediation_command: str = DEFAULT_SETUP_REMEDIATION,
) -> OnboardingSurfaceRoute:
    effective_has_tty = bool(has_tty) and not bool(no_interactive)
    status = OnboardingStatusService().inspect(
        OnboardingInspectionRequest(
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            config_arg=config_arg,
            agent_id=agent_id,
            requested_mode=requested_mode,
            has_tty=effective_has_tty,
            env=env,
            runtime_env=runtime_env,
            process_env=process_env,
            remediation_command=remediation_command,
        )
    )
    return OnboardingSurfaceRoute(
        status=status,
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
    )


def build_inline_setup_args(
    *,
    config: str | None,
    home_root: str | None,
    data_root: str | None,
    agent: str | None,
    no_chat: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        config=config,
        home_root=home_root,
        data_root=data_root,
        no_chat=no_chat,
        agent=agent,
    )


def format_fail_fast_message(*, surface: str, status: OnboardingStatus) -> str:
    remediation = str(status.remediation_command or "").strip()
    lines = [f"{surface}: error — {status.reason}"]
    if remediation:
        lines.append(f"run: {remediation}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SETUP_REMEDIATION",
    "OnboardingAction",
    "OnboardingInspectionRequest",
    "OnboardingPlan",
    "OnboardingPlanStep",
    "OnboardingRequestedMode",
    "OnboardingState",
    "OnboardingStatus",
    "OnboardingStatusService",
    "OnboardingTrack",
]
