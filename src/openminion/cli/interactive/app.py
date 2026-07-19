from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from textual.app import App

from openminion.base.config.runtime.profile import (
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_VALUES,
    next_permission_mode,
)
from openminion.cli.parser.contracts import (
    CLI_INTERFACE_VERSION,
    ensure_cli_component_compatibility,
)
from openminion.cli.presentation.animation import AnimationResolution
from openminion.cli.presentation.models import ChatMessage
from openminion.cli.status import TokenUsageSnapshot
from openminion.cli.theme import DARK, Theme
from openminion.cli.theme.textual_adapter import (
    theme_variables_dict,
)
from .onboarding import OnboardingWizardScreen
from .models import SidebarItem
from .screen import FocusScreen


class _DemoFocusRuntime:
    contract_version = CLI_INTERFACE_VERSION

    def __init__(
        self,
        *,
        working_dir: str,
        agent: str | None = None,
        session: str | None = None,
    ) -> None:
        self._working_dir = str(Path(working_dir).resolve(strict=False))
        self._agent_id = str(agent or "default")
        self._session_id = str(session or "").strip() or self._new_session_id()
        self._sessions: dict[str, list[ChatMessage]] = {self._session_id: []}
        self._permission_mode = ""
        self._permission_overrides: dict[str, str] = {}
        self._action_policy_mode_override = ""

    @staticmethod
    def _new_session_id() -> str:
        return f"sess-{uuid4().hex[:8]}"

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transport(self) -> str:
        return "demo(no-daemon)"

    @property
    def is_bound(self) -> bool:
        return True

    @property
    def working_dir(self) -> str:
        return self._working_dir

    @property
    def provider_name(self) -> str:
        return "echo"

    @property
    def model_name(self) -> str:
        return "demo"

    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        return TokenUsageSnapshot()

    async def send_message(self, text: str) -> AsyncIterator[str]:
        await asyncio.sleep(0.1)
        yield f"Demo response: {text}"

    def get_current_history(self) -> list[ChatMessage]:
        return list(self._sessions.get(self._session_id, []))

    def list_sessions(self) -> list[SidebarItem]:
        return [
            SidebarItem(
                id=session_id,
                label=session_id[:12],
                active=session_id == self._session_id,
            )
            for session_id in self._sessions
        ]

    def list_agents(self) -> list[SidebarItem]:
        return [SidebarItem(self._agent_id, self._agent_id, active=True)]

    def list_tools(self) -> list[tuple[str, bool]]:
        return []

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        self._session_id = session_id
        self._sessions.setdefault(session_id, [])
        return self.get_current_history()

    def switch_agent(self, agent_id: str) -> None:
        self._agent_id = str(agent_id or "default")

    def new_session(self) -> str:
        self._session_id = self._new_session_id()
        self._sessions[self._session_id] = []
        return self._session_id

    @property
    def permission_mode(self) -> str:
        return self._permission_mode or PERMISSION_MODE_DEFAULT

    def set_permission_mode(self, mode: str) -> str:
        normalized = str(mode or PERMISSION_MODE_DEFAULT).strip().lower()
        if normalized not in PERMISSION_MODE_VALUES:
            valid = ", ".join(sorted(PERMISSION_MODE_VALUES))
            raise ValueError(f"unknown permission mode {mode!r}; valid modes: {valid}")
        self._permission_mode = (
            "" if normalized == PERMISSION_MODE_DEFAULT else normalized
        )
        return self.permission_mode

    def cycle_permission_mode(self) -> str:
        return self.set_permission_mode(next_permission_mode(self.permission_mode))

    @property
    def action_policy_mode_override(self) -> str:
        return self._action_policy_mode_override

    def set_session_action_policy_mode(self, mode: str) -> str:
        self._action_policy_mode_override = str(mode or "")
        return self._action_policy_mode_override

    @property
    def permission_overrides(self) -> dict[str, str]:
        return dict(self._permission_overrides)

    def set_permission_override(self, tool_name: str, mode: str) -> str:
        self._permission_overrides[str(tool_name)] = str(mode)
        return str(mode)

    def bind_session(self, session_id: str) -> None:
        self.switch_session(session_id)

    def create_new_session(self) -> str:
        return self.new_session()

    def find_candidate_session(self):
        return None

    def list_directory_sessions(self, *, limit: int = 20):
        del limit
        return []


class FocusApp(App):
    CSS_PATH = [
        Path(__file__).parent / "foundation.tcss",
        Path(__file__).parent / "styles.tcss",
    ]
    TITLE = "OpenMinion CLI"
    _active_theme: Theme = DARK
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self,
        *,
        runtime=None,
        working_dir: str | None = None,
        agent: str | None = None,
        session: str | None = None,
        onboarding_request: dict | None = None,
        theme: Theme | None = None,
        verbosity: str = "normal",
        progress: str = "full",
        animation: AnimationResolution | None = None,
    ) -> None:
        self._active_theme: Theme = theme if isinstance(theme, Theme) else DARK
        super().__init__()
        resolved_dir = str(Path(working_dir or ".").expanduser().resolve(strict=False))
        self._runtime = runtime or _DemoFocusRuntime(
            working_dir=resolved_dir,
            agent=agent,
            session=session,
        )
        self._working_dir = resolved_dir
        self._agent = str(agent or "").strip() or None
        self._session = str(session or "").strip() or None
        self._onboarding_request = onboarding_request
        self._verbosity: str = (
            verbosity if verbosity in ("quiet", "normal", "verbose") else "normal"
        )
        self._progress: str = progress if progress in ("full", "minimal", "off") else "full"
        self._animation = animation

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
        ensure_cli_component_compatibility(
            self._runtime,
            component_type="chat_runtime",
        )
        self.push_screen(
            FocusScreen(
                runtime=self._runtime,
                working_dir=self._working_dir,
                requested_agent=self._agent,
                requested_session=self._session,
                verbosity=self._verbosity,
                progress=self._progress,
                animation=self._animation,
            )
        )
