from __future__ import annotations

from pathlib import Path

from openminion.api.runtime import APIRuntime
import pytest

from openminion.cli.parser.contracts import (
    CLI_INTERFACE_VERSION,
    ProviderBundle,
    ensure_cli_component_compatibility,
    ensure_provider_bundle_compatibility,
)

_MINIMAX_CONFIG_PATH = str(
    (
        Path(__file__).resolve().parents[4]
        / "test-configs"
        / "per-agent-alibaba-minimax.json"
    ).resolve(strict=False)
)
_HOME_ROOT = str(Path(__file__).resolve().parents[4])


def test_provider_bundle_all_demo_is_contract_compatible() -> None:
    bundle = ProviderBundle.all_demo()

    assert bundle.tasks is not None
    assert bundle.cron is not None
    assert bundle.sessions is not None
    assert bundle.system is not None
    assert bundle.policy is not None
    assert bundle.memory is not None
    assert bundle.provider is not None
    assert bundle.agents is not None

    ensure_provider_bundle_compatibility(bundle)


def test_provider_bundle_all_demo_shares_approval_state() -> None:
    bundle = ProviderBundle.all_demo()
    assert bundle.tasks is not None
    assert bundle.policy is not None

    pending = bundle.tasks.list_pending_actions()
    assert pending
    decision_id = pending[0]["decision_id"]

    assert bundle.tasks.resolve_action(decision_id, "allow") is True
    policy_pending = bundle.policy.list_pending_decisions()
    assert all(item.get("id") != decision_id for item in policy_pending)


def test_provider_bundle_from_api_runtime_is_contract_compatible() -> None:
    rt = APIRuntime.from_config_path(
        _MINIMAX_CONFIG_PATH,
        home_root=_HOME_ROOT,
    )
    try:
        bundle = ProviderBundle.from_api_runtime(rt)
        assert bundle.tasks is not None
        assert bundle.cron is not None
        assert bundle.sessions is not None
        assert bundle.system is not None
        assert bundle.policy is not None
        assert bundle.memory is not None
        assert bundle.provider is not None
        assert bundle.agents is not None
        ensure_provider_bundle_compatibility(bundle)
    finally:
        rt.close()


def test_provider_bundle_compatibility_rejects_missing_agents_contract_method() -> None:
    class _BrokenAgentsProvider:
        contract_version = CLI_INTERFACE_VERSION

        def list_agents(self) -> list[dict]:
            return []

        def get_agent_detail(self, agent_id: str) -> dict:
            return {}

        def get_agent_tools(self, agent_id: str) -> list[dict]:
            return []

        def upsert_profile(self, profile_dict: dict) -> str:
            return "v1"

        def delete_profile(self, agent_id: str) -> None:
            return None

    bundle = ProviderBundle(agents=_BrokenAgentsProvider())

    with pytest.raises(TypeError, match="create_default_profile"):
        ensure_provider_bundle_compatibility(bundle)


def test_provider_contract_checks_cover_session_and_cron_mutation_methods() -> None:
    class _BrokenSessionsProvider:
        contract_version = CLI_INTERFACE_VERSION

        def list_all_sessions(self) -> list[dict]:
            return []

        def get_session_timeline(self, session_id: str) -> list[dict]:
            return []

        def close_session(self, session_id: str) -> None:
            return None

        def delete_session(self, session_id: str) -> None:
            return None

    class _BrokenCronProvider:
        contract_version = CLI_INTERFACE_VERSION

        def list_jobs(self) -> list[dict]:
            return []

        def list_recent_runs(self, job_id: str, limit: int = 10) -> list[dict]:
            return []

    with pytest.raises(TypeError, match="update_session_name"):
        ensure_cli_component_compatibility(
            _BrokenSessionsProvider(),
            component_type="sessions_provider",
        )

    with pytest.raises(TypeError, match="toggle_job_enabled"):
        ensure_cli_component_compatibility(
            _BrokenCronProvider(),
            component_type="cron_provider",
        )


def test_provider_contract_checks_require_session_delete_method() -> None:
    class _BrokenSessionsProvider:
        contract_version = CLI_INTERFACE_VERSION

        def list_all_sessions(self) -> list[dict]:
            return []

        def get_session_timeline(self, session_id: str) -> list[dict]:
            return []

        def close_session(self, session_id: str) -> None:
            return None

        def update_session_name(self, session_id: str, name: str) -> None:
            return None

    with pytest.raises(TypeError, match="delete_session"):
        ensure_cli_component_compatibility(
            _BrokenSessionsProvider(),
            component_type="sessions_provider",
        )
