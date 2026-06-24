from __future__ import annotations

from pathlib import Path

from openminion.api.runtime import APIRuntime
from openminion.cli.parser.contracts import (
    ProviderBundle,
    ensure_cli_component_compatibility,
    ensure_provider_bundle_compatibility,
)
from openminion.cli.tui.providers.runtime import OpenMinionRuntime

_MINIMAX_CONFIG_PATH = str(
    (
        Path(__file__).resolve().parents[4]
        / "test-configs"
        / "per-agent-alibaba-minimax.json"
    ).resolve(strict=False)
)
_HOME_ROOT = str(Path(__file__).resolve().parents[4])


def test_runtime_and_provider_bundle_contracts_from_api_runtime() -> None:
    rt = APIRuntime.from_config_path(
        _MINIMAX_CONFIG_PATH,
        home_root=_HOME_ROOT,
    )
    try:
        tui_runtime = OpenMinionRuntime(rt)
        ensure_cli_component_compatibility(tui_runtime, component_type="chat_runtime")

        bundle = ProviderBundle.from_api_runtime(rt)
        ensure_provider_bundle_compatibility(bundle)

        assert bundle.tasks is not None
        assert bundle.cron is not None
        assert bundle.sessions is not None
        assert bundle.system is not None
        assert bundle.policy is not None
        assert bundle.memory is not None
        assert bundle.provider is not None
        assert bundle.agents is not None

        ensure_cli_component_compatibility(
            bundle.tasks, component_type="tasks_provider"
        )
        ensure_cli_component_compatibility(bundle.cron, component_type="cron_provider")
        ensure_cli_component_compatibility(
            bundle.sessions, component_type="sessions_provider"
        )
        ensure_cli_component_compatibility(
            bundle.system, component_type="system_provider"
        )
        ensure_cli_component_compatibility(
            bundle.policy, component_type="policy_provider"
        )
        ensure_cli_component_compatibility(
            bundle.memory, component_type="memory_provider"
        )
        ensure_cli_component_compatibility(
            bundle.provider, component_type="third_brain_provider"
        )
        ensure_cli_component_compatibility(
            bundle.agents, component_type="agents_provider"
        )

        # Basic smoke calls to ensure each adapter executes without raising.
        assert isinstance(bundle.tasks.list_tasks(), list)
        assert isinstance(bundle.tasks.list_pending_actions(), list)
        assert isinstance(bundle.cron.list_jobs(), list)
        assert isinstance(bundle.cron.toggle_job_enabled("missing-job", True), bool)
        assert isinstance(bundle.sessions.list_all_sessions(), list)
        bundle.sessions.update_session_name("missing-session", "renamed")
        bundle.sessions.close_session("missing-session")
        bundle.sessions.delete_session("missing-session")
        assert isinstance(bundle.system.get_daemon_status(), dict)
        assert isinstance(bundle.policy.list_recent_decisions(), list)
        assert isinstance(bundle.memory.list_records(limit=5), list)
        assert isinstance(bundle.provider.list_provider_status(), list)
        assert isinstance(bundle.agents.list_agents(), list)
    finally:
        rt.close()
