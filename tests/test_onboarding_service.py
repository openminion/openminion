from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


from pathlib import Path

from openminion.base.config import OpenMinionConfig, save_config
from openminion.services.bootstrap.onboarding import (
    OnboardingAction,
    OnboardingInspectionRequest,
    OnboardingPlanStep,
    OnboardingRequestedMode,
    OnboardingState,
    OnboardingStatusService,
    OnboardingTrack,
)


def _write_config(
    tmp_path: Path,
    *,
    provider: str,
    api_key: str = "",
    api_key_env: str = "",
    runtime_env: dict[str, str] | None = None,
) -> Path:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider=provider)
    config.storage.path = str((tmp_path / ".openminion" / "state" / "openminion.db"))
    config.runtime.env = dict(runtime_env or {})
    if provider in {"anthropic", "claude"}:
        config.providers.anthropic.api_key = api_key
        config.providers.anthropic.api_key_env = api_key_env
    elif provider == "openai":
        config.providers.openai.api_key = api_key
        config.providers.openai.api_key_env = api_key_env
    config_path = tmp_path / ".openminion" / "agents.json"
    save_config(config, str(config_path), home_root=tmp_path)
    return config_path


def test_onboarding_status_missing_config_interactive_launches_setup(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    request = OnboardingInspectionRequest(
        config_path=tmp_path / ".openminion" / "agents.json",
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        has_tty=True,
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.MISSING_CONFIG
    assert status.action == OnboardingAction.LAUNCH_SETUP
    assert status.track == OnboardingTrack.UNKNOWN
    assert status.can_continue is False
    assert plan.launches_setup is True
    assert plan.interactive is True
    assert plan.steps == (
        OnboardingPlanStep.LAUNCH_SETUP,
        OnboardingPlanStep.SELECT_TRACK,
        OnboardingPlanStep.SELECT_PROVIDER,
        OnboardingPlanStep.RUN_DOCTOR,
        OnboardingPlanStep.ENTER_CHAT,
    )
    assert "Default config file not found at" in status.reason
    assert str(tmp_path / ".openminion" / "agents.json") in status.reason


def test_onboarding_status_missing_explicit_relative_config_fails_fast_with_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = OnboardingStatusService()
    monkeypatch.chdir(tmp_path)
    request = OnboardingInspectionRequest(
        config_path=tmp_path / "missing.json",
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_arg="missing.json",
        has_tty=True,
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.MISSING_CONFIG
    assert status.action == OnboardingAction.FAIL_FAST
    assert "Config file does not exist:" in status.reason
    assert str(tmp_path / "missing.json") in status.reason
    assert "missing.json" in status.reason
    assert "current working directory" in status.reason
    assert status.remediation_command == ""
    assert plan.steps == (OnboardingPlanStep.FAIL_WITH_REMEDIATION,)


def test_onboarding_status_missing_explicit_config_suggests_nearby_file(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    suggested = config_dir / "per-agent-openrouter-claude-haiku-3.json"
    suggested.write_text("{}", encoding="utf-8")
    request = OnboardingInspectionRequest(
        config_path=config_dir / "per-agent-openrouter-claude-3-haiku.json",
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_arg=str(config_dir / "per-agent-openrouter-claude-3-haiku.json"),
        has_tty=True,
    )

    status = service.inspect(request)

    assert status.state == OnboardingState.MISSING_CONFIG
    assert status.action == OnboardingAction.FAIL_FAST
    assert f"Did you mean {suggested.resolve(strict=False)}?" in status.reason
    assert status.remediation_command == ""


def test_onboarding_status_missing_config_noninteractive_fails_fast(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    request = OnboardingInspectionRequest(
        config_path=tmp_path / ".openminion" / "agents.json",
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        has_tty=False,
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.MISSING_CONFIG
    assert status.action == OnboardingAction.FAIL_FAST
    assert status.remediation_command == "openminion setup"
    assert plan.steps == (OnboardingPlanStep.FAIL_WITH_REMEDIATION,)
    assert plan.interactive is False


def test_onboarding_status_explicit_demo_intent_bypasses_setup(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    request = OnboardingInspectionRequest(
        config_path=tmp_path / ".openminion" / "agents.json",
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        requested_mode=OnboardingRequestedMode.DEMO,
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.EXPLICIT_DEMO
    assert status.action == OnboardingAction.CONTINUE
    assert status.track == OnboardingTrack.DEMO
    assert status.can_continue is True
    assert plan.steps == (
        OnboardingPlanStep.BYPASS_ONBOARDING,
        OnboardingPlanStep.ENTER_CHAT,
    )


def test_onboarding_status_detects_incomplete_cloud_credentials(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    config_path = _write_config(
        tmp_path,
        provider="anthropic",
        api_key="",
        api_key_env="ANTHROPIC_API_KEY",
    )
    request = OnboardingInspectionRequest(
        config_path=config_path,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        has_tty=True,
        process_env={},
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.INCOMPLETE_CLOUD_CREDENTIALS
    assert status.action == OnboardingAction.LAUNCH_SETUP
    assert status.track == OnboardingTrack.CLOUD
    assert status.provider_name == "anthropic"
    assert status.credentials_ready is False
    assert status.required_env_vars == ("ANTHROPIC_API_KEY",)
    assert plan.steps == (
        OnboardingPlanStep.LAUNCH_SETUP,
        OnboardingPlanStep.SELECT_TRACK,
        OnboardingPlanStep.SELECT_PROVIDER,
        OnboardingPlanStep.COLLECT_CREDENTIALS,
        OnboardingPlanStep.RUN_DOCTOR,
        OnboardingPlanStep.ENTER_CHAT,
    )


def test_onboarding_status_classifies_ready_cloud_path(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    config_path = _write_config(
        tmp_path,
        provider="openai",
        api_key="",
        api_key_env="OPENAI_API_KEY",
    )
    request = OnboardingInspectionRequest(
        config_path=config_path,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        process_env={"OPENAI_API_KEY": "sk-test"},
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.READY
    assert status.action == OnboardingAction.CONTINUE
    assert status.track == OnboardingTrack.CLOUD
    assert status.provider_name == "openai"
    assert status.storage_ready is True
    assert status.credentials_ready is True
    assert plan.steps == (
        OnboardingPlanStep.BYPASS_ONBOARDING,
        OnboardingPlanStep.ENTER_CHAT,
    )


def test_onboarding_status_uses_config_runtime_env_for_provider_credentials(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    config_path = _write_config(
        tmp_path,
        provider="openai",
        api_key="",
        api_key_env="DASHSCOPE_API_KEY",
        runtime_env={"DASHSCOPE_API_KEY": "sk-test"},
    )
    request = OnboardingInspectionRequest(
        config_path=config_path,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        process_env={},
    )

    status = service.inspect(request)

    assert status.state == OnboardingState.READY
    assert status.action == OnboardingAction.CONTINUE
    assert status.provider_name == "openai"
    assert status.credentials_ready is True
    assert status.required_env_vars == ("DASHSCOPE_API_KEY",)


def test_onboarding_status_invalid_requested_profile_fails_fast(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
                "planner-safe": {"name": "planner-safe", "provider": "openai"},
            },
            "default_agent": "hello-agent",
        }
    )
    config_path = tmp_path / ".openminion" / "agents.json"
    save_config(config, str(config_path), home_root=tmp_path)

    status = service.inspect(
        OnboardingInspectionRequest(
            config_path=config_path,
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            agent_id="missing-profile",
            has_tty=True,
        )
    )

    assert status.state == OnboardingState.CONFIG_ERROR
    assert status.action == OnboardingAction.FAIL_FAST
    assert "missing-profile" in status.reason
    assert "planner-safe" in status.reason


def test_onboarding_status_missing_default_config_mentions_legacy_default_rename(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    legacy_path = tmp_path / ".openminion" / ("agent.json")
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("{}", encoding="utf-8")

    status = service.inspect(
        OnboardingInspectionRequest(
            config_path=tmp_path / ".openminion" / "agents.json",
            home_root=tmp_path,
            data_root=tmp_path / ".openminion",
            has_tty=True,
        )
    )

    assert status.state == OnboardingState.MISSING_CONFIG
    assert "Legacy default config detected" in status.reason
    assert str(legacy_path) in status.reason
    assert "Rename it to `agents.json`" in status.reason


def test_onboarding_status_classifies_ollama_local_path(
    tmp_path: Path,
) -> None:
    service = OnboardingStatusService()
    config_path = _write_config(tmp_path, provider="ollama")
    request = OnboardingInspectionRequest(
        config_path=config_path,
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        process_env={},
    )

    status = service.inspect(request)
    plan = service.build_plan(status)

    assert status.state == OnboardingState.LOCAL_OLLAMA
    assert status.action == OnboardingAction.CONTINUE
    assert status.track == OnboardingTrack.LOCAL
    assert status.provider_name == "ollama"
    assert status.credentials_ready is True
    assert plan.steps == (
        OnboardingPlanStep.VALIDATE_LOCAL_RUNTIME,
        OnboardingPlanStep.ENTER_CHAT,
    )
