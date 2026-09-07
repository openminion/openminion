from __future__ import annotations

import io
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from openminion.api.core.profiles import AgentConfigActivationError
from openminion.cli.interactive.terminal.shell.model_setup import handle_model_setup
from openminion.modules.llm.setup_catalog import get_setup_preset
from openminion.services.bootstrap.provider_setup import (
    ProviderSetupConnectionConflict,
    ProviderSetupMissingCredential,
)

pytestmark = pytest.mark.e2e


class _Overlay:
    def __init__(self, prompts: list[str | None], confirms: list[bool]) -> None:
        self.prompts = prompts
        self.confirms = confirms
        self.secret_prompts: list[bool] = []

    async def present_prompt_async(
        self,
        _prompt: str,
        *,
        secret: bool = False,
    ) -> str | None:
        self.secret_prompts.append(secret)
        return self.prompts.pop(0)

    async def present_confirm_async(
        self,
        _prompt: str,
        *,
        default: bool = False,
    ) -> bool:
        del default
        return self.confirms.pop(0)


class _Runtime:
    def __init__(self, preset_id: str) -> None:
        self.preset = get_setup_preset(preset_id)
        self.build_calls: list[dict[str, Any]] = []
        self.applied: list[Any] = []
        self.failures: list[Exception] = []
        self.apply_error: Exception | None = None

    def model_setup_presets(self) -> tuple[Any, ...]:
        return (self.preset,)

    def build_model_setup(self, **kwargs: Any) -> Any:
        self.build_calls.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(
            preview=SimpleNamespace(
                display_label=self.preset.display_label,
                connection_id=kwargs["connection_id"],
                api_format_label=self.preset.api_format_label,
                model=kwargs["model"],
                base_url=kwargs["base_url"] or self.preset.default_base_url,
                credential=(
                    "local config (redacted)"
                    if kwargs["stored_api_key"]
                    else "not required"
                ),
                config_path="/tmp/config.json",
            )
        )

    def apply_model_setup(self, result: Any) -> Any:
        self.applied.append(result)
        if self.apply_error is not None:
            raise self.apply_error
        return SimpleNamespace(
            connection_name=result.preview.display_label,
            model=result.preview.model,
        )


def _console() -> tuple[Console, io.StringIO]:
    output = io.StringIO()
    return Console(file=output, force_terminal=False, width=120), output


@pytest.mark.asyncio
async def test_model_setup_saves_and_selects_known_preset() -> None:
    runtime = _Runtime("ollama")
    overlay = _Overlay(["1", ""], [True])
    console, output = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    assert runtime.build_calls == [
        {
            "preset_id": "ollama",
            "model": "llama3.1",
            "base_url": "",
            "connection_id": "ollama",
            "stored_api_key": "",
            "allow_local_api_key": False,
        }
    ]
    assert len(runtime.applied) == 1
    assert "selected for this session; not tested yet" in output.getvalue()


@pytest.mark.asyncio
async def test_model_setup_masks_local_credential_after_consent() -> None:
    secret = "fixture-secret-value"
    runtime = _Runtime("minimax")
    runtime.failures.append(
        ProviderSetupMissingCredential("MiniMax", "MINIMAX_API_KEY")
    )
    overlay = _Overlay(["minimax", "", secret], [True, True])
    console, output = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    assert runtime.build_calls[-1]["stored_api_key"] == secret
    assert runtime.build_calls[-1]["allow_local_api_key"] is True
    assert overlay.secret_prompts[-1] is True
    assert secret not in output.getvalue()
    assert "local config (redacted)" in output.getvalue()


@pytest.mark.asyncio
async def test_model_setup_requires_new_id_for_connection_collision() -> None:
    runtime = _Runtime("ollama")
    runtime.failures.append(ProviderSetupConnectionConflict("ollama"))
    overlay = _Overlay(["ollama", "", "ollama-local"], [True])
    console, _ = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    assert [call["connection_id"] for call in runtime.build_calls] == [
        "ollama",
        "ollama-local",
    ]
    assert len(runtime.applied) == 1


@pytest.mark.asyncio
async def test_model_setup_cancellation_does_not_build_or_apply() -> None:
    runtime = _Runtime("ollama")
    overlay = _Overlay([None], [])
    console, output = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    assert runtime.build_calls == []
    assert runtime.applied == []
    assert "cancelled" in output.getvalue()


@pytest.mark.asyncio
async def test_model_setup_rejects_unknown_connection_once() -> None:
    runtime = _Runtime("ollama")
    overlay = _Overlay(["missing"], [])
    console, output = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    rendered = output.getvalue()
    assert "unknown connection 'missing'" in rendered
    assert "cancelled" not in rendered
    assert runtime.build_calls == []
    assert runtime.applied == []


@pytest.mark.asyncio
async def test_model_setup_declined_local_credential_does_not_apply() -> None:
    runtime = _Runtime("minimax")
    runtime.failures.append(
        ProviderSetupMissingCredential("MiniMax", "MINIMAX_API_KEY")
    )
    overlay = _Overlay(["minimax", ""], [False])
    console, output = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    assert runtime.applied == []
    assert True not in overlay.secret_prompts
    assert "cancelled" in output.getvalue()


@pytest.mark.asyncio
async def test_model_setup_accepts_custom_endpoint() -> None:
    runtime = _Runtime("custom-openai-compatible")
    runtime.preset = replace(
        runtime.preset,
        credential_env="",
        is_local=True,
    )
    overlay = _Overlay(
        [
            "custom-openai-compatible",
            "custom-model",
            "https://models.example.test/v1",
        ],
        [True],
    )
    console, _ = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    call = runtime.build_calls[0]
    assert call["model"] == "custom-model"
    assert call["base_url"] == "https://models.example.test/v1"
    assert call["connection_id"] == "custom-openai-compatible"
    assert len(runtime.applied) == 1


@pytest.mark.asyncio
async def test_model_setup_reports_saved_but_not_activated() -> None:
    runtime = _Runtime("ollama")
    runtime.apply_error = AgentConfigActivationError(
        "Configuration was saved, but activating it failed; restart OpenMinion."
    )
    overlay = _Overlay(["ollama", ""], [True])
    console, output = _console()

    await handle_model_setup(runtime=runtime, console=console, overlay=overlay)

    rendered = output.getvalue()
    assert "saved, but activating it failed" in rendered
    assert "selected for this session" not in rendered
