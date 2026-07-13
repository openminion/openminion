from __future__ import annotations

from pathlib import Path

from textual.app import App

from openminion.cli.parser.contracts import ensure_cli_component_compatibility
from openminion.cli.theme import DARK, Theme
from openminion.cli.theme.textual_adapter import (
    theme_variables_dict,
)
from openminion.cli.tui.app import DemoRuntime
from openminion.cli.tui.screen import OnboardingWizardScreen

from .screen import FocusScreen


class _DemoFocusRuntime(DemoRuntime):
    def __init__(
        self,
        *,
        working_dir: str,
        agent: str | None = None,
        session: str | None = None,
    ) -> None:
        super().__init__()
        self._working_dir = str(Path(working_dir).resolve(strict=False))
        if agent:
            self.switch_agent(agent)
        if session:
            self.switch_session(session)

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
        Path(__file__).parents[1] / "tui" / "styles.tcss",
        Path(__file__).parent / "styles.tcss",
    ]
    TITLE = "OpenMinion Focus"
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
            )
        )
