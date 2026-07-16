from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import QueryError
from textual.screen import Screen
from textual.widgets import Button, Input, Label


class OnboardingWizardScreen(Screen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        config_path: Path,
        home_root: Path,
        data_root: Path,
        agent_id: str,
    ) -> None:
        super().__init__()
        self._config_path = Path(config_path)
        self._home_root = Path(home_root)
        self._data_root = Path(data_root)
        self._agent_id = str(agent_id or "openminion").strip() or "openminion"
        self._step = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="onboarding-overlay"):
            with Vertical(id="onboarding-dialog"):
                yield Label("OpenMinion First-Run Setup", classes="modal-title")
                yield Label("Step 1 of 3 - config path", id="onboarding-step-label")
                with Vertical(id="onboarding-step-config", classes="wizard-step"):
                    yield Label("Config path:")
                    yield Input(value=str(self._config_path), id="onboarding-config-path")
                with Vertical(
                    id="onboarding-step-provider",
                    classes="wizard-step --hidden",
                ):
                    yield Label("Provider:")
                    yield Input(
                        value="openrouter",
                        id="onboarding-provider",
                        placeholder="openrouter / openai / anthropic / ollama / echo",
                    )
                    yield Label("Model:")
                    yield Input(
                        value="anthropic/claude-3-haiku",
                        id="onboarding-model",
                        placeholder="Model name",
                    )
                with Vertical(
                    id="onboarding-step-agent",
                    classes="wizard-step --hidden",
                ):
                    yield Label("Initial agent id:")
                    yield Input(
                        value=self._agent_id,
                        id="onboarding-agent-id",
                        placeholder="hello-agent",
                    )
                with Horizontal(id="onboarding-buttons"):
                    yield Button("Back", id="onboarding-back")
                    yield Button("Next", id="onboarding-next", variant="primary")
                    yield Button("Cancel", id="onboarding-cancel")

    def on_mount(self) -> None:
        self._sync_step()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "onboarding-back":
            self._step = max(0, self._step - 1)
            self._sync_step()
            return
        if button_id == "onboarding-next":
            if self._step < 2:
                self._step += 1
                self._sync_step()
                return
            self._finish()
            return
        self.app.exit(result="")

    def action_cancel(self) -> None:
        self.app.exit(result="")

    def _sync_step(self) -> None:
        labels = {
            0: "Step 1 of 3 - config path",
            1: "Step 2 of 3 - provider and model",
            2: "Step 3 of 3 - initial agent",
        }
        step_ids = (
            "onboarding-step-config",
            "onboarding-step-provider",
            "onboarding-step-agent",
        )
        try:
            self.query_one("#onboarding-step-label", Label).update(labels[self._step])
        except (QueryError, AttributeError):
            pass
        for index, step_id in enumerate(step_ids):
            try:
                step = self.query_one(f"#{step_id}", Vertical)
            except (QueryError, AttributeError):
                continue
            step.set_class(index != self._step, "--hidden")
        try:
            back = self.query_one("#onboarding-back", Button)
            back.disabled = self._step == 0
            next_button = self.query_one("#onboarding-next", Button)
            next_button.label = "Create config" if self._step == 2 else "Next"
        except (QueryError, AttributeError):
            pass
        focus_ids = {
            0: "#onboarding-config-path",
            1: "#onboarding-provider",
            2: "#onboarding-agent-id",
        }
        try:
            self.query_one(focus_ids[self._step], Input).focus()
        except (QueryError, AttributeError):
            pass

    def _finish(self) -> None:
        from openminion.base.config import AgentProfileConfig, OpenMinionConfig, save_config

        config_path = Path(
            self.query_one("#onboarding-config-path", Input).value.strip()
        ).expanduser()
        provider = str(
            self.query_one("#onboarding-provider", Input).value.strip() or "openrouter"
        ).lower()
        model = str(
            self.query_one("#onboarding-model", Input).value.strip()
            or "anthropic/claude-3-haiku"
        )
        agent_id = str(
            self.query_one("#onboarding-agent-id", Input).value.strip()
            or self._agent_id
        )

        config = OpenMinionConfig()
        config.runtime.demo_mode = provider == "echo"
        config.storage.path = str(
            (self._data_root / "state" / "openminion.db").resolve(strict=False)
        )
        if provider == "openai":
            config.providers.openai.model = model
        elif provider == "anthropic":
            config.providers.anthropic.model = model
        elif provider == "openrouter":
            config.providers.openrouter.model = model
        elif provider == "ollama":
            config.providers.ollama.model = model
        config.agents = {
            agent_id: AgentProfileConfig(name=agent_id, provider=provider)
        }
        saved_path = save_config(config, str(config_path), home_root=self._home_root)
        self.app.exit(result=str(saved_path))
