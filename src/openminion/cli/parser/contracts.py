from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openminion.api.runtime import APIRuntime

CLI_INTERFACE_VERSION = "v1"


@runtime_checkable
class AgentRuntimeAPI(Protocol):
    """Minimal contract every TUI variant needs."""

    contract_version: str  # must equal CLI_INTERFACE_VERSION

    @property
    def agent_id(self) -> str: ...
    @property
    def session_id(self) -> str: ...
    @property
    def transport(self) -> str: ...

    def get_current_history(self) -> list[Any]:
        """Return list[ChatMessage] for the current session."""
        ...

    def list_sessions(self) -> list[Any]:
        """Return list[SidebarItem]."""
        ...

    def list_agents(self) -> list[Any]:
        """Return list[SidebarItem]."""
        ...

    def list_tools(self) -> list[tuple[str, bool]]:
        """Return list of (name, enabled) pairs."""
        ...

    def switch_session(self, session_id: str) -> list[Any]:
        """Switch active session; return new session's history."""
        ...

    def switch_agent(self, agent_id: str) -> None: ...

    def new_session(self) -> str:
        """Create and activate a new session; return its id."""
        ...


@runtime_checkable
class ChatRuntimeAPI(AgentRuntimeAPI, Protocol):
    """Extends `AgentRuntimeAPI` with streaming chat turn support."""

    async def send_message(self, text: str) -> AsyncIterator[str]:
        """Yield response chunks.  Implementations must be async generators."""
        ...


@runtime_checkable
class TasksProviderAPI(Protocol):
    """Data provider for the Tasks tab."""

    contract_version: str

    def list_tasks(self) -> list[dict[str, Any]]:
        """Return list of task dicts: id, title, status, due_at, steps."""
        ...

    def list_pending_actions(self) -> list[dict[str, Any]]:
        """Return pending approval actions across all tasks."""
        ...

    def resolve_action(self, decision_id: str, outcome: str) -> bool:
        """Resolve a pending action. outcome: 'allow' | 'deny'. Returns True on success."""
        ...


@runtime_checkable
class CronProviderAPI(Protocol):
    """Data provider for the Cron tab."""

    contract_version: str

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return list of cron job dicts: id, expr, next_due, enabled."""
        ...

    def list_recent_runs(self, job_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent run records for a specific job."""
        ...

    def toggle_job_enabled(self, job_id: str, enabled: bool) -> bool:
        """Enable or disable a cron job. Returns True on success."""
        ...


@runtime_checkable
class SessionsProviderAPI(Protocol):
    """Data provider for the Sessions tab."""

    contract_version: str

    def list_all_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions with metadata: id, age, turn_count, agent_id, channel, name."""
        ...

    def get_session_timeline(self, session_id: str) -> list[dict[str, Any]]:
        """Return ordered event timeline for a session."""
        ...

    def close_session(self, session_id: str) -> None:
        """Close/archive a session by id.  No-op if already closed."""
        ...

    def delete_session(self, session_id: str) -> None:
        """Hard-delete a session by id. No-op if it does not exist."""
        ...

    def update_session_name(self, session_id: str, name: str) -> None:
        """Update the session display name. Empty name clears the custom label."""
        ...


@runtime_checkable
class SystemProviderAPI(Protocol):
    """Data provider for the System tab."""

    contract_version: str

    def get_daemon_status(self) -> dict[str, Any]:
        """Return daemon process info: mode, pid, endpoint, uptime."""
        ...

    def get_storage_stats(self) -> dict[str, Any]:
        """Return storage metrics: db_size, session_count, event_count."""
        ...

    def get_agent_info(self) -> dict[str, Any]:
        """Return active agent config: model, runtime_mode, brain_mode, provider."""
        ...

    def get_telemetry_summary(self) -> dict[str, Any]:
        """Return event counts / latency for the last hour."""
        ...

    def get_plugin_status(self) -> list[dict[str, Any]]:
        """Return list of plugin dicts: name, enabled."""
        ...


@runtime_checkable
class PolicyProviderAPI(Protocol):
    """Data provider for the Policy tab."""

    contract_version: str

    def list_pending_decisions(self) -> list[dict[str, Any]]:
        """Return decisions awaiting human approval: id, tool, reason, risk."""
        ...

    def list_active_grants(self) -> list[dict[str, Any]]:
        """Return active policy grants: id, scope, ttl, max_uses, uses_left."""
        ...

    def list_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent decision history: id, tool, outcome, ts."""
        ...

    def revoke_grant(self, grant_id: str) -> bool:
        """Revoke an active grant. Returns True on success."""
        ...


@runtime_checkable
class MemoryProviderAPI(Protocol):
    """Data provider for the Memory tab.

    Maps to modules/memory/interfaces.py MemoryServiceInterface.
    Phase 1 real implementation adapts the live memory service directly.
    """

    contract_version: str

    def list_records(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return memory records: id, type, scope, content_preview, ts."""
        ...

    def list_candidates(self) -> list[dict[str, Any]]:
        """Return unconfirmed candidate memories awaiting promotion."""
        ...

    def search(self, query: str) -> list[dict[str, Any]]:
        """Keyword/semantic search over memory records."""
        ...


@runtime_checkable
class ThirdBrainProviderAPI(Protocol):
    """Data provider for the Third Brain tab."""

    contract_version: str

    def list_provider_status(self) -> list[dict[str, Any]]:
        """Return provider health/capability metadata for third-brain sources."""
        ...

    def search(
        self,
        query: str,
        *,
        provider_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Return provider-neutral query result envelopes."""
        ...

    def neighborhood(
        self,
        entity_id: str,
        *,
        provider_names: list[str] | None = None,
        depth: int = 1,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Return provider-neutral neighborhood result envelopes."""
        ...

    def path(
        self,
        source_entity_id: str,
        target_entity_id: str,
        *,
        provider_names: list[str] | None = None,
        max_hops: int = 4,
    ) -> list[dict[str, Any]]:
        """Return provider-neutral path result envelopes."""
        ...

    def refresh(
        self,
        *,
        provider_names: list[str] | None = None,
        full: bool = False,
    ) -> list[dict[str, Any]]:
        """Return provider-neutral refresh result envelopes."""
        ...


@runtime_checkable
class AgentsProviderAPI(Protocol):
    """Data provider for the Agents tab — profile CRUD + tools per agent."""

    contract_version: str

    def list_agents(self) -> list[dict[str, Any]]:
        """Return agent summaries: id, display_name, provider, is_hot, revision."""
        ...

    def get_agent_detail(self, agent_id: str) -> dict[str, Any]:
        """Return full profile + config for an agent."""
        ...

    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        """Return tools available to this agent (with posture filtering applied)."""
        ...

    def upsert_profile(self, profile_dict: dict[str, Any]) -> str:
        """Save/update an agent profile. Returns version hash."""
        ...

    def delete_profile(self, agent_id: str) -> None:
        """Delete an agent profile."""
        ...

    def create_default_profile(
        self, agent_id: str, display_name: str
    ) -> dict[str, Any]:
        """Create a new profile with sensible defaults. Returns the profile dict."""
        ...


@dataclass
class ProviderBundle:
    tasks: TasksProviderAPI | None = None
    cron: CronProviderAPI | None = None
    sessions: SessionsProviderAPI | None = None
    system: SystemProviderAPI | None = None
    policy: PolicyProviderAPI | None = None
    memory: MemoryProviderAPI | None = None
    provider: ThirdBrainProviderAPI | None = None
    agents: AgentsProviderAPI | None = None

    @classmethod
    def all_demo(cls) -> "ProviderBundle":
        from openminion.cli.tui.app import (
            DemoAgentsProvider,
            DemoCronProvider,
            DemoMemoryProvider,
            DemoPolicyProvider,
            DemoSessionsProvider,
            DemoSystemProvider,
            DemoTasksProvider,
            _MockApprovalStore,
        )
        from openminion.cli.tui.providers.thirdbrain import (
            DemoThirdBrainProvider,
        )

        approval_store = _MockApprovalStore()
        return cls(
            tasks=DemoTasksProvider(approval_store),
            cron=DemoCronProvider(),
            sessions=DemoSessionsProvider(),
            system=DemoSystemProvider(),
            policy=DemoPolicyProvider(approval_store),
            memory=DemoMemoryProvider(),
            provider=DemoThirdBrainProvider(),
            agents=DemoAgentsProvider(),
        )

    @staticmethod
    def _resolve_runtime_agent_id(rt: "APIRuntime") -> str:
        from openminion.base.config.core import resolve_default_agent_id

        try:
            agent_id = resolve_default_agent_id(rt.config)
        except Exception:
            agent_id = ""
        if not agent_id:
            agent_id = str(
                getattr(getattr(rt, "agent", None), "agent_id", "") or ""
            ).strip()
        return agent_id or "default"

    @staticmethod
    def _resolve_runtime_session_id(rt: "APIRuntime", *, agent_id: str) -> tuple:
        sessions = getattr(rt, "sessions", None)
        if sessions is None or not callable(getattr(sessions, "resolve_session", None)):
            return sessions, ""
        try:
            session = sessions.resolve_session(
                agent_id=agent_id,
                channel="cli",
                target="tui",
            )
            return sessions, str(getattr(session, "id", "") or "").strip()
        except Exception:
            return sessions, ""

    @staticmethod
    def _resolve_runtime_task_ctl(rt: "APIRuntime"):
        return (
            getattr(rt, "task_ctl", None)
            or getattr(getattr(rt, "agent", None), "task_ctl", None)
            or getattr(getattr(rt, "agent", None), "_task_ctl", None)
        )

    @staticmethod
    def _resolve_runtime_cron_repository(rt: "APIRuntime"):
        from openminion.modules.session.storage.repository import (
            create_sqlite_cron_repository,
        )

        storage_path = getattr(rt, "storage_path", None)
        if not storage_path:
            return None
        try:
            return create_sqlite_cron_repository(db_path=storage_path)
        except Exception:
            return None

    @staticmethod
    def _resolve_runtime_memory_service(rt: "APIRuntime"):
        memory_service = getattr(rt, "memory_service", None)
        if memory_service is not None:
            return memory_service
        gateway = getattr(rt, "gateway", None)
        gateway_memory = getattr(gateway, "_agent_memory", None)
        if gateway_memory is None:
            return None
        if all(
            callable(getattr(gateway_memory, method, None))
            for method in ("list", "search", "candidate_list")
        ):
            return gateway_memory
        return getattr(gateway_memory, "_service", None)

    @classmethod
    def from_api_runtime(cls, rt: "APIRuntime") -> "ProviderBundle":
        from openminion.cli.tui.providers import (
            RuntimeAgentsProvider,
            RuntimeCronProvider,
            RuntimeMemoryProvider,
            RuntimePolicyProvider,
            RuntimeSessionsProvider,
            RuntimeSystemProvider,
            RuntimeTasksProvider,
            RuntimeThirdBrainProvider,
        )

        agent_id = cls._resolve_runtime_agent_id(rt)
        sessions, session_id = cls._resolve_runtime_session_id(rt, agent_id=agent_id)
        task_ctl = cls._resolve_runtime_task_ctl(rt)
        cron_repository = cls._resolve_runtime_cron_repository(rt)
        memory_service = cls._resolve_runtime_memory_service(rt)

        return cls(
            tasks=RuntimeTasksProvider(
                task_ctl, agent_id=agent_id, session_id=session_id
            ),
            cron=RuntimeCronProvider(cron_repository),
            sessions=RuntimeSessionsProvider(sessions),
            system=RuntimeSystemProvider(rt),
            policy=RuntimePolicyProvider(getattr(rt, "action_policy", None)),
            memory=RuntimeMemoryProvider(
                memory_service,
                agent_id=agent_id,
                session_id=session_id,
            ),
            provider=RuntimeThirdBrainProvider(getattr(rt, "knowledge_graphs", None)),
            agents=RuntimeAgentsProvider(rt),
        )


_PROPERTY_MEMBERS = {"agent_id", "session_id", "transport"}

_REQUIRED: dict[str, tuple[str, ...]] = {
    "agent_runtime": (
        "agent_id",
        "session_id",
        "transport",
        "get_current_history",
        "list_sessions",
        "list_agents",
        "list_tools",
        "switch_session",
        "switch_agent",
        "new_session",
    ),
    "chat_runtime": (
        "agent_id",
        "session_id",
        "transport",
        "send_message",
        "get_current_history",
        "list_sessions",
        "list_agents",
        "list_tools",
        "switch_session",
        "switch_agent",
        "new_session",
    ),
    "tui_runtime": (
        "agent_id",
        "session_id",
        "transport",
        "send_message",
        "get_current_history",
        "list_sessions",
        "list_agents",
        "list_tools",
        "switch_session",
        "switch_agent",
        "new_session",
    ),
    "tasks_provider": ("list_tasks", "list_pending_actions", "resolve_action"),
    "cron_provider": ("list_jobs", "list_recent_runs", "toggle_job_enabled"),
    "sessions_provider": (
        "list_all_sessions",
        "get_session_timeline",
        "close_session",
        "delete_session",
        "update_session_name",
    ),
    "system_provider": (
        "get_daemon_status",
        "get_storage_stats",
        "get_agent_info",
        "get_telemetry_summary",
        "get_plugin_status",
    ),
    "policy_provider": (
        "list_pending_decisions",
        "list_active_grants",
        "list_recent_decisions",
    ),
    "memory_provider": ("list_records", "list_candidates", "search"),
    "third_brain_provider": (
        "list_provider_status",
        "search",
        "neighborhood",
        "path",
        "refresh",
    ),
    "agents_provider": (
        "list_agents",
        "get_agent_detail",
        "get_agent_tools",
        "upsert_profile",
        "delete_profile",
        "create_default_profile",
    ),
}


def ensure_cli_component_compatibility(
    component: object,
    *,
    component_type: str,
) -> None:
    if component_type not in _REQUIRED:
        raise ValueError(
            f"unknown cli component_type {component_type!r}; valid: {sorted(_REQUIRED)}"
        )

    errors: list[str] = []
    for member in _REQUIRED[component_type]:
        if not hasattr(component, member):
            errors.append(f"missing '{member}'")
        elif member not in _PROPERTY_MEMBERS:
            val = getattr(component, member)
            if not callable(val):
                errors.append(f"'{member}' must be callable")

    version = getattr(component, "contract_version", None)
    if version != CLI_INTERFACE_VERSION:
        errors.append(
            f"contract_version mismatch: got={version!r} "
            f"expected={CLI_INTERFACE_VERSION!r}"
        )

    if errors:
        raise TypeError(
            f"{component.__class__.__name__} incompatible with "
            f"cli/{component_type} contract: {'; '.join(errors)}"
        )


def ensure_provider_bundle_compatibility(bundle: ProviderBundle) -> None:
    checks: list[tuple[object | None, str]] = [
        (bundle.tasks, "tasks_provider"),
        (bundle.cron, "cron_provider"),
        (bundle.sessions, "sessions_provider"),
        (bundle.system, "system_provider"),
        (bundle.policy, "policy_provider"),
        (bundle.memory, "memory_provider"),
        (bundle.provider, "third_brain_provider"),
        (bundle.agents, "agents_provider"),
    ]
    for provider, component_type in checks:
        if provider is not None:
            ensure_cli_component_compatibility(provider, component_type=component_type)
