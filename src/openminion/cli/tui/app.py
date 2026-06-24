from __future__ import annotations

import asyncio
import random
import string
from pathlib import Path
from typing import AsyncIterator

from textual.app import App
from textual.css.query import QueryError

from openminion.cli.theme import DARK, Theme
from openminion.cli.theme.textual_adapter import (
    theme_variables_dict,
)
from openminion.cli.parser.contracts import (
    CLI_INTERFACE_VERSION,
    ProviderBundle,
    ensure_cli_component_compatibility,
    ensure_provider_bundle_compatibility,
)
from openminion.cli.status import TokenUsageSnapshot
from .screen import MainScreen
from .providers.thirdbrain import DemoThirdBrainProvider
from .widgets import ChatMessage, MessageKind, SidebarItem


class DemoRuntime:
    contract_version: str = CLI_INTERFACE_VERSION

    _RESPONSES = [
        "Demo response: request received.",
        "Demo response: sample answer payload.",
        (
            "Demo tool transcript: `browser` returned sample content.\n"
            "Summary:\n"
            "  • Item one from the page\n"
            "  • Item two from the page\n"
            "  • Item three from the page"
        ),
        "Demo response: contextual placeholder.",
        "Demo response: task completed.",
        "Demo search transcript: [search_brave]\nTop demo results…",
    ]

    def __init__(self) -> None:
        self._agent_id = "default"
        self._session_id = "sess-" + _rand_id()
        self._transport = "demo(no-daemon)"
        self._sessions: dict[str, list[ChatMessage]] = {
            self._session_id: _demo_history(self._agent_id),
        }
        self._agents = ["default", "agent-02"]
        self._tools = [
            ("browser", True),
            ("search_brave", True),
            ("file", True),
            ("fetch", True),
            ("exec", False),
            ("weather_openmeteo", True),
            ("location", False),
        ]

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return self._transport

    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        return TokenUsageSnapshot()

    async def send_message(self, text: str) -> AsyncIterator[str]:
        await asyncio.sleep(0.4)
        yield random.choice(self._RESPONSES)

    def get_current_history(self) -> list[ChatMessage]:
        return self._sessions.get(self._session_id, [])

    def list_sessions(self) -> list[SidebarItem]:
        items: list[SidebarItem] = []
        for sid, messages in self._sessions.items():
            preview_lines = [
                f"{message.sender}: {message.body[:40]}"
                for message in messages[-3:]
                if message.kind in {MessageKind.USER, MessageKind.AGENT}
            ]
            items.append(
                SidebarItem(
                    sid,
                    sid[:12],
                    active=(sid == self._session_id),
                    meta={"preview_lines": preview_lines},
                )
            )
        return items

    def list_agents(self) -> list[SidebarItem]:
        return [SidebarItem(a, a, active=(a == self._agent_id)) for a in self._agents]

    def list_tools(self) -> list[tuple[str, bool]]:
        return list(self._tools)

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        self._session_id = session_id
        return self._sessions.get(session_id, [])

    def switch_agent(self, agent_id: str) -> None:
        if agent_id in self._agents:
            self._agent_id = agent_id

    def new_session(self) -> str:
        new_id = "sess-" + _rand_id()
        self._sessions[new_id] = []
        self._session_id = new_id
        return new_id


class _MockApprovalStore:
    def __init__(self) -> None:
        self._pending: list[dict] = [
            {
                "id": "dec-001",
                "tool": "exec",
                "reason": "rm -rf /tmp/old_build",
                "risk": "HIGH",
                "task_id": "task-003",
            },
        ]
        self._recent: list[dict] = [
            {"id": "dec-000", "tool": "file.read", "outcome": "allow", "ts": "10:05"},
            {"id": "dec-001", "tool": "exec", "outcome": "pending", "ts": "10:21"},
        ]

    def list_pending_decisions(self) -> list[dict]:
        return [dict(item) for item in self._pending]

    def list_recent_decisions(self, limit: int = 20) -> list[dict]:
        return [dict(item) for item in self._recent[:limit]]

    def resolve(self, decision_id: str, outcome: str) -> bool:
        decision = next(
            (item for item in self._pending if item.get("id") == decision_id), None
        )
        if decision is None:
            return False

        self._pending = [
            item for item in self._pending if item.get("id") != decision_id
        ]
        self._recent = [item for item in self._recent if item.get("id") != decision_id]
        self._recent.insert(
            0,
            {
                "id": decision_id,
                "tool": decision.get("tool", ""),
                "outcome": outcome,
                "ts": "now",
            },
        )
        return True


class DemoTasksProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, approval_store: _MockApprovalStore) -> None:
        self._approval_store = approval_store

    def list_tasks(self) -> list[dict]:
        pending_actions = [
            {
                "decision_id": item.get("id", ""),
                "reason": item.get("reason", ""),
                "tool": item.get("tool", ""),
                "task_id": item.get("task_id", ""),
            }
            for item in self._approval_store.list_pending_decisions()
            if item.get("task_id") == "task-003"
        ]
        return [
            {
                "id": "task-001",
                "title": "Refactor auth module",
                "status": "ACTIVE",
                "steps": [
                    {
                        "order_index": 1,
                        "title": "Audit existing code",
                        "status": "DONE",
                    },
                    {
                        "order_index": 2,
                        "title": "Extract interfaces",
                        "status": "ACTIVE",
                    },
                    {"order_index": 3, "title": "Write tests", "status": "PENDING"},
                    {"order_index": 4, "title": "Update docs", "status": "PENDING"},
                ],
            },
            {
                "id": "task-002",
                "title": "Deploy pipeline update",
                "status": "ACTIVE",
                "steps": [],
            },
            {
                "id": "task-003",
                "title": "Review PR #42",
                "status": "WAITING",
                "due_at": "2026-03-16",
                "steps": [],
                "pending_actions": pending_actions,
            },
            {
                "id": "task-004",
                "title": "Write release notes",
                "status": "PENDING",
                "steps": [],
            },
            {"id": "task-005", "title": "Update README", "status": "DONE", "steps": []},
        ]

    def list_pending_actions(self) -> list[dict]:
        return [
            {
                "decision_id": item.get("id", ""),
                "reason": item.get("reason", ""),
                "tool": item.get("tool", ""),
                "task_id": item.get("task_id", ""),
            }
            for item in self._approval_store.list_pending_decisions()
        ]

    def resolve_action(self, decision_id: str, outcome: str) -> bool:
        return self._approval_store.resolve(decision_id, outcome)


class DemoCronProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._jobs = [
            {
                "id": "daily-summary",
                "expr": "0 9 * * *",
                "next_due": "tomorrow 09:00 UTC",
                "enabled": True,
                "misfire_policy": "skip",
                "recent_runs": [
                    {"at": "03-15 09:00", "state": "success", "duration": "1m 58s"},
                    {"at": "03-14 09:00", "state": "success", "duration": "2m 02s"},
                    {"at": "03-13 09:00", "state": "timeout", "duration": "—"},
                ],
            },
            {
                "id": "weekly-gc",
                "expr": "0 0 * * 0",
                "next_due": "Sun 00:00 UTC",
                "enabled": True,
                "misfire_policy": "run_once",
                "recent_runs": [
                    {"at": "03-10 00:00", "state": "success", "duration": "4m 11s"},
                ],
            },
            {
                "id": "mem-refresh",
                "expr": "*/30 * * * *",
                "next_due": "in 14 minutes",
                "enabled": False,
                "misfire_policy": "skip",
                "recent_runs": [],
            },
        ]

    def list_jobs(self) -> list[dict]:
        return [dict(job) for job in self._jobs]

    def list_recent_runs(self, job_id: str, limit: int = 10) -> list[dict]:
        jobs = {j["id"]: j for j in self.list_jobs()}
        return jobs.get(job_id, {}).get("recent_runs", [])

    def toggle_job_enabled(self, job_id: str, enabled: bool) -> bool:
        for job in self._jobs:
            if job.get("id") == job_id:
                job["enabled"] = bool(enabled)
                return True
        return False


class DemoPolicyProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, approval_store: _MockApprovalStore) -> None:
        self._approval_store = approval_store
        self._grants = [
            {
                "id": "grant-001",
                "scope": "exec:/tmp/*",
                "ttl": "expires in 4h",
                "max_uses": 5,
                "uses_left": 3,
            },
        ]

    def list_pending_decisions(self) -> list[dict]:
        return self._approval_store.list_pending_decisions()

    def list_active_grants(self) -> list[dict]:
        return [dict(grant) for grant in self._grants]

    def list_recent_decisions(self, limit: int = 20) -> list[dict]:
        return self._approval_store.list_recent_decisions(limit=limit)

    def revoke_grant(self, grant_id: str) -> bool:
        before = len(self._grants)
        self._grants = [grant for grant in self._grants if grant.get("id") != grant_id]
        return len(self._grants) != before


class DemoAgentsProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._profiles: list[dict] = [
            {
                "id": "alibaba-minimax",
                "display_name": "Alibaba MiniMax",
                "provider": "openai",
                "is_hot": True,
                "revision": 3,
            },
            {
                "id": "researcher",
                "display_name": "Research Agent",
                "provider": "anthropic",
                "is_hot": False,
                "revision": 1,
            },
            {
                "id": "code-reviewer",
                "display_name": "Code Review Agent",
                "provider": "anthropic",
                "is_hot": False,
                "revision": 2,
            },
        ]

    def list_agents(self) -> list[dict]:
        return list(self._profiles)

    def get_agent_detail(self, agent_id: str) -> dict:
        for a in self._profiles:
            if a["id"] == agent_id:
                return {
                    **a,
                    "agent_id": agent_id,
                    "thinking": "minimal",
                    "channel": "cli",
                    "runtime_mode": "in-process",
                    "profile": {
                        "agent_id": agent_id,
                        "display_name": a["display_name"],
                        "profile_revision": a["revision"],
                        "role": {
                            "mission": "A helpful assistant.",
                            "responsibilities": ["Answer questions", "Execute tasks"],
                            "hard_constraints": ["Do not delete system files"],
                            "domain": ["general", "coding"],
                        },
                        "personality": {
                            "tone": "professional",
                            "verbosity": "normal",
                            "formatting": ["markdown"],
                            "interaction_style": ["direct"],
                        },
                        "risk": {
                            "risk_level": "medium",
                            "confirm_before": ["file_write", "shell_exec"],
                        },
                        "tool_posture": {
                            "tool_use": "allowed",
                            "allowed_tools": [],
                            "blocked_patterns": ["file_delete"],
                        },
                    },
                }
        return {"agent_id": agent_id}

    def get_agent_tools(self, agent_id: str) -> list[dict]:
        tools = [
            "brave_search",
            "file_read",
            "file_write",
            "shell_exec",
            "file_delete",
            "memory_search",
            "calendar_check",
        ]
        blocked = {"file_delete"}
        return [{"name": t, "allowed": t not in blocked} for t in sorted(tools)]

    def render_identity_preview(
        self,
        agent_id: str,
        *,
        purpose: str = "act",
        max_tokens: int = 256,
    ) -> str:
        detail = self.get_agent_detail(agent_id)
        profile = detail.get("profile", {})
        role = profile.get("role", {})
        personality = profile.get("personality", {})
        return (
            f"Agent: {agent_id}\n"
            f"Display: {profile.get('display_name', agent_id)}\n\n"
            f"Mission\n{role.get('mission', 'A helpful assistant.')}\n\n"
            f"Personality\n"
            f"Tone: {personality.get('tone', 'professional')}\n"
            f"Verbosity: {personality.get('verbosity', 'normal')}"
        )

    def upsert_profile(self, profile_dict: dict) -> str:
        aid = profile_dict.get("agent_id", "")
        for i, a in enumerate(self._profiles):
            if a["id"] == aid:
                self._profiles[i]["display_name"] = profile_dict.get(
                    "display_name", a["display_name"]
                )
                self._profiles[i]["revision"] = profile_dict.get(
                    "profile_revision", a.get("revision", 0)
                )
                return "demo-version-hash"
        return "demo-version-hash"

    def delete_profile(self, agent_id: str) -> None:
        self._profiles = [a for a in self._profiles if a["id"] != agent_id]

    def create_default_profile(self, agent_id: str, display_name: str) -> dict:
        entry = {
            "id": agent_id,
            "display_name": display_name or agent_id,
            "provider": "",
            "is_hot": False,
            "revision": 1,
        }
        self._profiles.append(entry)
        return {
            "agent_id": agent_id,
            "display_name": display_name or agent_id,
            "profile_revision": 1,
            "role": {"mission": "A helpful assistant.", "responsibilities": []},
            "personality": {"tone": "professional", "verbosity": "normal"},
            "risk": {"risk_level": "medium", "confirm_before": []},
            "tool_posture": {
                "tool_use": "allowed",
                "allowed_tools": [],
                "blocked_patterns": [],
            },
        }


class DemoMemoryProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def list_records(self, limit: int = 50) -> list[dict]:
        return [
            {
                "id": "mem-001",
                "type": "episodic",
                "scope": "session",
                "content_preview": "User prefers concise answers",
                "content": "User prefers concise answers and quick bullet summaries.",
                "metadata": {"source": "chat", "confidence": "high"},
                "ts": "2026-03-14",
            },
            {
                "id": "mem-002",
                "type": "semantic",
                "scope": "global",
                "content_preview": "Project uses Textual 8.x TUI",
                "content": "Project uses Textual 8.x for the terminal UI stack.",
                "metadata": {"source": "repo", "area": "frontend"},
                "ts": "2026-03-15",
            },
            {
                "id": "mem-003",
                "type": "working",
                "scope": "session",
                "content_preview": "Current task: refactor auth",
                "content": "Current task: refactor auth and preserve runtime compatibility.",
                "metadata": {"source": "planner", "status": "active"},
                "ts": "2026-03-15",
            },
        ]

    def list_candidates(self) -> list[dict]:
        return [
            {
                "id": "cand-001",
                "content_preview": "User timezone is UTC+9",
                "score": 0.82,
            },
            {
                "id": "cand-002",
                "content_preview": "Preferred model: claude-opus",
                "score": 0.71,
            },
        ]

    def search(self, query: str) -> list[dict]:
        return [
            r
            for r in self.list_records()
            if query.lower() in r["content_preview"].lower()
        ]


class DemoSessionsProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._deleted: set[str] = set()

    def list_all_sessions(self) -> list[dict]:
        sessions = [
            {
                "id": "sess-abc123",
                "age": "2h",
                "turn_count": 12,
                "agent_id": "default",
                "channel": "cli",
                "name": self._names.get("sess-abc123", ""),
                "participants": [
                    {
                        "participant_type": "human",
                        "participant_id": "owner",
                        "role": "owner",
                        "channel": "cli",
                    },
                    {
                        "participant_type": "agent",
                        "participant_id": "default",
                        "role": "participant",
                        "channel": "cli",
                    },
                ],
            },
            {
                "id": "sess-def456",
                "age": "1d",
                "turn_count": 4,
                "agent_id": "agent-02",
                "channel": "cli",
                "name": self._names.get("sess-def456", ""),
                "participants": [
                    {
                        "participant_type": "agent",
                        "participant_id": "agent-02",
                        "role": "owner",
                        "channel": "cli",
                    }
                ],
            },
            {
                "id": "sess-ghi789",
                "age": "3d",
                "turn_count": 28,
                "agent_id": "default",
                "channel": "tui",
                "name": self._names.get("sess-ghi789", ""),
                "participants": [
                    {
                        "participant_type": "human",
                        "participant_id": "observer",
                        "role": "observer",
                        "channel": "tui",
                    },
                    {
                        "participant_type": "agent",
                        "participant_id": "default",
                        "role": "participant",
                        "channel": "tui",
                    },
                ],
            },
            {
                "id": "sess-jkl012",
                "age": "5d",
                "turn_count": 7,
                "agent_id": "default",
                "channel": "cli",
                "name": self._names.get("sess-jkl012", ""),
                "participants": [
                    {
                        "participant_type": "agent",
                        "participant_id": "default",
                        "role": "participant",
                        "channel": "cli",
                    }
                ],
            },
        ]
        return [session for session in sessions if session["id"] not in self._deleted]

    def get_session_timeline(self, session_id: str) -> list[dict]:
        return [
            {"ts": "10:21", "event_type": "context.manifest.created", "detail": ""},
            {
                "ts": "10:21",
                "event_type": "llm.call.started",
                "detail": "default / claude-opus-4-6",
            },
            {"ts": "10:22", "event_type": "tool.request", "detail": "search_brave"},
            {"ts": "10:22", "event_type": "tool.response", "detail": "✓ 5 results"},
            {
                "ts": "10:23",
                "event_type": "llm.call.completed",
                "detail": "1842 tokens",
            },
            {"ts": "10:23", "event_type": "memory.turn.recorded", "detail": ""},
        ]

    def close_session(self, session_id: str) -> None:
        self._deleted.add(session_id)

    def delete_session(self, session_id: str) -> None:
        self._names.pop(session_id, None)
        self._deleted.add(session_id)

    def update_session_name(self, session_id: str, name: str) -> None:
        self._names[session_id] = name


class DemoSystemProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._sidecar_running = False
        self._sidecar_consent = "approved"

    def get_daemon_status(self) -> dict:
        return {"mode": "demo(no-daemon)", "endpoint": "—", "pid": "—", "uptime": "—"}

    def get_agent_info(self) -> dict:
        return {
            "model": "claude-opus-4-6",
            "runtime_mode": "brain",
            "brain_mode": "contextctl_authoritative",
            "provider": "anthropic",
        }

    def get_storage_stats(self) -> dict:
        return {
            "db_size": "—",
            "session_count": 4,
            "event_count": "—",
            "memory_count": "—",
        }

    def get_telemetry_summary(self) -> dict:
        return {"turns": "—", "tool_calls": "—", "errors": "—", "avg_latency": "—"}

    def get_plugin_status(self) -> list[dict]:
        return [
            {"name": "memory-capsule", "enabled": True},
            {"name": "context-compression", "enabled": True},
            {"name": "rlm", "enabled": False},
        ]

    def get_sidecar_status(self) -> dict:
        return {
            "name": "pinchtab",
            "running": self._sidecar_running,
            "pid": 43210 if self._sidecar_running else "—",
            "consent": self._sidecar_consent,
        }

    def set_sidecar_consent(self, approved: bool) -> dict:
        self._sidecar_consent = "approved" if approved else "denied"
        return self.get_sidecar_status()

    def start_sidecar(self) -> dict:
        self._sidecar_running = True
        return self.get_sidecar_status()

    def stop_sidecar(self) -> dict:
        self._sidecar_running = False
        return self.get_sidecar_status()


def _rand_id(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _demo_history(agent_id: str) -> list[ChatMessage]:
    return [
        ChatMessage(
            kind=MessageKind.SYSTEM,
            sender="system",
            body="Welcome to OpenMinion TUI  —  demo mode",
        ),
        ChatMessage(
            kind=MessageKind.USER,
            sender="you",
            body="Hello! What can you help me with?",
        ),
        ChatMessage(
            kind=MessageKind.AGENT,
            sender=agent_id,
            body=(
                "Demo agent capabilities:\n"
                "  • answer questions and help with research\n"
                "  • browse the web and fetch pages\n"
                "  • search using Brave Search\n"
                "  • read and write files\n"
                "  • type /tools to see the available demo tools"
            ),
        ),
        ChatMessage(
            kind=MessageKind.USER,
            sender="you",
            body="Can you search for the latest Python news?",
        ),
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:search_brave",
            body="search_brave(query='latest Python news')",
            tool_result="→ 5 results returned",
        ),
        ChatMessage(
            kind=MessageKind.AGENT,
            sender=agent_id,
            body=(
                "Here are the latest Python highlights:\n"
                "  1. Python 3.13 released with improved performance\n"
                "  2. PEP 730 accepted — iOS support\n"
                "  3. uv package manager gains widespread adoption"
            ),
        ),
    ]


class OpenMinionApp(App):
    CSS_PATH = Path(__file__).parent / "styles.tcss"
    TITLE = "OpenMinion"
    ENABLE_COMMAND_PALETTE = False
    _active_theme: Theme = DARK

    def __init__(
        self,
        runtime=None,
        providers: ProviderBundle | None = None,
        no_picker: bool = False,
        initial_tab: str | None = None,
        onboarding_request: dict | None = None,
        theme: Theme | None = None,
    ) -> None:
        self._active_theme: Theme = theme if isinstance(theme, Theme) else DARK
        super().__init__()
        self._runtime = runtime or DemoRuntime()
        self._no_picker = no_picker
        self._initial_tab = initial_tab
        self._onboarding_request = onboarding_request
        approval_store = _MockApprovalStore()
        self._providers = providers or ProviderBundle(
            tasks=DemoTasksProvider(approval_store),
            cron=DemoCronProvider(),
            sessions=DemoSessionsProvider(),
            system=DemoSystemProvider(),
            policy=DemoPolicyProvider(approval_store),
            memory=DemoMemoryProvider(),
            provider=DemoThirdBrainProvider(),
            agents=DemoAgentsProvider(),
        )

    @property
    def active_theme(self) -> Theme:
        return self._active_theme

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(theme_variables_dict(self._active_theme))
        return variables

    def apply_theme(self, theme: Theme) -> bool:
        if not isinstance(theme, Theme):
            return False
        try:
            previous = self._active_theme
            self._active_theme = theme
            self.refresh_css(animate=False)
            return True
        except Exception:
            self._active_theme = previous
            return False

    def on_mount(self) -> None:
        if self._onboarding_request is not None:
            from .screen import OnboardingWizardScreen

            self.push_screen(
                OnboardingWizardScreen(
                    config_path=Path(
                        str(self._onboarding_request.get("config_path", ""))
                    ),
                    home_root=Path(str(self._onboarding_request.get("home_root", ""))),
                    data_root=Path(str(self._onboarding_request.get("data_root", ""))),
                    agent_id=str(
                        self._onboarding_request.get("agent_id", "openminion")
                    ),
                )
            )
            return
        contract = (
            "chat_runtime"
            if hasattr(self._runtime, "send_message")
            else "agent_runtime"
        )
        ensure_cli_component_compatibility(self._runtime, component_type=contract)
        ensure_provider_bundle_compatibility(self._providers)
        self.push_screen(
            MainScreen(
                self._runtime,
                self._providers,
                initial_tab=self._initial_tab,
            )
        )

        from openminion.cli.parser.contracts import ChatRuntimeAPI

        runtime_type_name = type(self._runtime).__name__.strip().lower()
        is_demo = (
            isinstance(self._runtime, DemoRuntime)
            or runtime_type_name == "demoruntime"
            or "demo" in str(self._runtime.transport).lower()
        )
        has_chat = isinstance(self._runtime, ChatRuntimeAPI)
        if (
            not self._no_picker
            and not is_demo
            and has_chat
            and self._has_session_picker_sessions()
        ):
            self._show_session_picker()

    def _has_session_picker_sessions(self) -> bool:
        sessions_provider = self._providers.sessions
        if sessions_provider is None:
            return False
        list_all = getattr(sessions_provider, "list_all_sessions", None)
        if not callable(list_all):
            return False
        try:
            return len(list_all()) > 0
        except Exception:
            return False

    def _show_session_picker(self) -> None:
        from .widgets.session_picker import SessionPickerModal

        sessions_provider = self._providers.sessions
        sessions: list[dict] = []
        if sessions_provider is not None:
            list_all = getattr(sessions_provider, "list_all_sessions", None)
            if callable(list_all):
                try:
                    sessions = list_all()
                except Exception:
                    sessions = []

        def _on_pick(session_id: str | None) -> None:
            if session_id is None:
                return
            from .screen import MainScreen as _MainScreen
            from .tabs.chat import ChatTab
            from textual.widgets import TabbedContent

            screen = self.screen
            if isinstance(screen, _MainScreen) and screen._has_chat:
                try:
                    screen.query_one(TabbedContent).active = "tab-chat"
                    screen.query_one(ChatTab)._do_switch_session(session_id)
                except (QueryError, AttributeError):
                    pass

        self.push_screen(SessionPickerModal(sessions), _on_pick)


def run_demo() -> None:
    OpenMinionApp().run()


if __name__ == "__main__":
    run_demo()
