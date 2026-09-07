"""Terminal Focus interaction for adding a model connection."""

from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.api.core.profiles import AgentConfigActivationError
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.styles import StyleToken
from openminion.services.bootstrap.provider_setup import (
    ProviderSetupConnectionConflict,
    ProviderSetupError,
    ProviderSetupMissingCredential,
)

from ..overlays import TerminalOverlayPresenter

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"


def _cancel(console: Console) -> None:
    console.print(Text("(model setup cancelled)", style=_MUTED_ITALIC_STYLE))


def _render_preview(console: Console, preview: Any) -> None:
    console.print(Text("Review", style="bold"))
    console.print(f"  Connection: {preview.display_label} ({preview.connection_id})")
    console.print(f"  API format: {preview.api_format_label}")
    console.print(f"  Model: {preview.model}")
    console.print(f"  Base URL: {preview.base_url or 'provider default'}")
    console.print(f"  Credential: {preview.credential}")
    console.print(f"  Config: {preview.config_path}")
    console.print("  Current agent default: unchanged")


async def _prompt_setup_selection(
    *,
    runtime: Any,
    console: Console,
    overlay: TerminalOverlayPresenter,
) -> tuple[Any, str, str, str] | None:
    presets = tuple(runtime.model_setup_presets())
    console.print(Text("Add a model connection", style="bold"))
    for index, preset in enumerate(presets, start=1):
        console.print(f"  {index}. {preset.display_label} ({preset.api_format_label})")

    choice = await overlay.present_prompt_async("Connection number or id: ")
    if not choice:
        return None
    preset = None
    if choice.isdigit() and 1 <= int(choice) <= len(presets):
        preset = presets[int(choice) - 1]
    if preset is None:
        preset = next((row for row in presets if row.preset_id == choice), None)
    if preset is None:
        raise ValueError(f"unknown connection {choice!r}")

    default_model = preset.recommended_models[0] if preset.recommended_models else ""
    model = await overlay.present_prompt_async(
        f"Model [{default_model}]: " if default_model else "Model: "
    )
    if model is None:
        return None
    model = model or default_model

    base_url = ""
    if preset.requires_base_url:
        base_url = await overlay.present_prompt_async("Base URL: ") or ""
        if not base_url:
            return None

    return preset, model, base_url, preset.preset_id


async def handle_model_setup(
    *,
    runtime: Any,
    console: Console,
    overlay: TerminalOverlayPresenter,
) -> None:
    try:
        selection = await _prompt_setup_selection(
            runtime=runtime,
            console=console,
            overlay=overlay,
        )
    except ValueError as exc:
        console.print(Text(f"(/model: {exc})", style=_ERR_STYLE))
        return
    if selection is None:
        _cancel(console)
        return
    preset, model, base_url, connection_id = selection

    def build(target_connection_id: str, *, stored_api_key: str = "") -> Any:
        return runtime.build_model_setup(
            preset_id=preset.preset_id,
            model=model,
            base_url=base_url,
            connection_id=target_connection_id,
            stored_api_key=stored_api_key,
            allow_local_api_key=bool(stored_api_key),
        )

    async def build_with_connection(
        target_connection_id: str,
        *,
        stored_api_key: str = "",
    ) -> Any | None:
        try:
            return build(target_connection_id, stored_api_key=stored_api_key)
        except ProviderSetupConnectionConflict as exc:
            replacement = await overlay.present_prompt_async(
                f"Connection id {exc.connection_id!r} is already used. New id: "
            )
            if not replacement:
                return None
            return build(replacement, stored_api_key=stored_api_key)

    try:
        result = await build_with_connection(connection_id)
    except ProviderSetupMissingCredential as exc:
        console.print(
            Text(
                f"No {exc.env_var} environment variable was found.",
                style=_MUTED_STYLE,
            )
        )
        if not await overlay.present_confirm_async(
            "Store an API key in the local config instead?",
            default=False,
        ):
            _cancel(console)
            return
        stored_api_key = await overlay.present_prompt_async("API key: ", secret=True)
        if not stored_api_key:
            _cancel(console)
            return
        try:
            result = await build_with_connection(
                connection_id,
                stored_api_key=stored_api_key,
            )
        except ProviderSetupError as credential_exc:
            console.print(Text(f"(/model: {credential_exc})", style=_ERR_STYLE))
            return
    except ProviderSetupError as exc:
        console.print(Text(f"(/model: {exc})", style=_ERR_STYLE))
        return
    if result is None:
        _cancel(console)
        return

    _render_preview(console, result.preview)
    if not await overlay.present_confirm_async("Save and use this connection now?"):
        _cancel(console)
        return

    try:
        selected = runtime.apply_model_setup(result)
    except (AgentConfigActivationError, OSError, ValueError) as exc:
        console.print(Text(f"(/model: {exc})", style=_ERR_STYLE))
        return
    console.print(
        Text(
            f"(model: saved {selected.connection_name} / {selected.model}; "
            "selected for this session; not tested yet)",
            style=_MUTED_ITALIC_STYLE,
        )
    )
