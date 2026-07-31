from __future__ import annotations

import argparse
import importlib
import logging
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.base.config import OpenMinionConfig, resolve_config_path
from openminion.base.config.env import resolve_environment_config
from openminion.cli.config import resolve_cli_roots
from openminion.cli.commands.config import (
    load_config_import,
    resolve_config_import_path,
)
from openminion.modules.llm.setup_catalog import (
    ProviderSetupPreset,
    SetupCatalogError,
    first_screen_presets,
    get_setup_preset,
    more_screen_presets,
)
from openminion.services.bootstrap.provider_setup import (
    ProviderSetupError,
    ProviderSetupRequest,
    ProviderSetupResult,
    atomic_save_setup_config,
    build_provider_setup,
    save_provider_setup,
)


@dataclass(frozen=True)
class SetupSelection:
    label: str
    value: str


def _prompt_choice(prompt: str, options: dict[str, SetupSelection]) -> SetupSelection:
    while True:
        print(prompt)
        for key, selection in options.items():
            print(f"  {key}. {selection.label}")
        answer = str(input("> ") or "").strip()
        selection = options.get(answer)
        if selection is not None:
            return selection
        print(
            "Invalid selection. Choose one of: "
            + ", ".join(sorted(options.keys()))
            + "."
        )


def _prompt_text(prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = str(input(f"{prompt}{suffix}: ") or "").strip()
    except EOFError:
        value = ""
    return value or default


def _prompt_required_text(prompt: str) -> str:
    while True:
        value = _prompt_text(prompt)
        if value:
            return value
        print("A value is required.")


def _prompt_secret(prompt: str) -> str:
    try:
        return str(getpass(f"{prompt}: ") or "").strip()
    except EOFError:
        return ""


def _prompt_confirm(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        value = str(input(f"{prompt}{suffix}: ") or "").strip().lower()
    except EOFError:
        value = ""
    if not value:
        return default
    return value in {"y", "yes"}


def _run_wizard(args) -> tuple[OpenMinionConfig, Path]:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    agent_name = str(getattr(args, "agent", "") or "").strip() or "openminion"
    provider_arg = str(getattr(args, "provider", "") or "").strip()
    if provider_arg:
        result = _build_non_interactive_setup(args, roots=roots, agent_name=agent_name)
        saved_path = save_provider_setup(result)
        return result.config, saved_path

    selection = _prompt_choice(
        "Choose your setup path:",
        {
            "1": SetupSelection(
                label="Hosted provider (OpenAI, Anthropic, OpenRouter, and more)",
                value="hosted",
            ),
            "2": SetupSelection(label="Local provider (Ollama)", value="ollama"),
            "3": SetupSelection(
                label="Import an existing OpenMinion config",
                value="import",
            ),
        },
    )

    if selection.value == "import":
        return _run_import_wizard(args, roots=roots)
    if selection.value == "ollama":
        preset = get_setup_preset("ollama")
        model = _prompt_model(preset)
        base_url = _prompt_text(
            "Ollama base URL",
            default=preset.default_base_url,
        )
        result = _build_interactive_setup(
            args,
            roots=roots,
            agent_name=agent_name,
            preset=preset,
            model=model,
            base_url=base_url,
        )
    else:
        preset = _prompt_provider_preset()
        model = _prompt_model(preset)
        base_url = _prompt_required_text("Base URL") if preset.requires_base_url else ""
        result = _build_interactive_setup(
            args,
            roots=roots,
            agent_name=agent_name,
            preset=preset,
            model=model,
            base_url=base_url,
        )

    _print_preview(result)
    if not _prompt_confirm("Create or repair this config?", default=True):
        raise ProviderSetupError("Setup cancelled before writing config.")
    setattr(
        args,
        "_provider_check_authorized",
        _prompt_provider_check(result.preset),
    )
    saved_path = save_provider_setup(result)
    return result.config, saved_path


def _prompt_provider_preset() -> ProviderSetupPreset:
    presets = first_screen_presets()
    options = {
        str(index): SetupSelection(
            label=preset.display_label,
            value=preset.preset_id,
        )
        for index, preset in enumerate(presets, start=1)
    }
    options[str(len(options) + 1)] = SetupSelection(
        label="More providers or a custom endpoint",
        value="more",
    )
    selection = _prompt_choice("Choose your model service:", options)
    if selection.value != "more":
        return get_setup_preset(selection.value)

    more = more_screen_presets()
    more_options = {
        str(index): SetupSelection(
            label=preset.display_label,
            value=preset.preset_id,
        )
        for index, preset in enumerate(more, start=1)
    }
    return get_setup_preset(
        _prompt_choice(
            "Choose another service or custom endpoint:",
            more_options,
        ).value
    )


def _prompt_model(preset: ProviderSetupPreset) -> str:
    if preset.discovery_posture == "manual":
        return _prompt_required_text("Model id")
    recommended = preset.recommended_models[0]
    return _prompt_text(
        f"Model (press Enter for the existing or recommended default: {recommended})"
    )


def _run_import_wizard(args, *, roots) -> tuple[OpenMinionConfig, Path]:
    target_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=roots.home_root,
    )
    try:
        input_path = resolve_config_import_path(
            _prompt_required_text("OpenMinion config file"),
            home_root=roots.home_root,
        )
        config = load_config_import(
            input_path,
            target_path=target_path,
            home_root=roots.home_root,
            data_root=roots.data_root,
            merge_existing=True,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProviderSetupError(str(exc)) from exc
    print("Import preview:")
    print(f"  source: {input_path}")
    print(f"  target: {target_path}")
    print(f"  agents: {len(config.agents)}")
    print(f"  default agent: {config.default_agent}")
    print("  existing config: unrelated settings are preserved")
    if not _prompt_confirm("Import this config?", default=True):
        raise ProviderSetupError("Setup cancelled before importing config.")
    setattr(args, "_provider_check_authorized", False)
    return config, atomic_save_setup_config(config, target_path)


def _build_interactive_setup(
    args,
    *,
    roots,
    agent_name: str,
    preset: ProviderSetupPreset,
    model: str,
    base_url: str,
) -> ProviderSetupResult:
    env_snapshot = resolve_environment_config().snapshot()
    stored_api_key = ""
    allow_local = False
    env_has_key = bool(
        preset.credential_env and env_snapshot.get(preset.credential_env)
    )
    if preset.requires_credential and not env_has_key:
        print(
            f"No {preset.credential_env} environment variable was found. "
            "Paste a key to store it in this local config, or press Enter to cancel."
        )
        stored_api_key = _prompt_secret(f"{preset.display_label} API key")
        if not stored_api_key:
            raise ProviderSetupError(
                f"No credential was provided. Export {preset.credential_env} "
                "and rerun setup."
            )
        print(
            "This key will be stored only in the owner-readable OpenMinion config. "
            "Environment variables remain preferable on shared machines."
        )
        allow_local = _prompt_confirm(
            "Store this key locally?",
            default=False,
        )
        if not allow_local:
            raise ProviderSetupError(
                f"Local key storage was not approved. Export {preset.credential_env} "
                "and rerun setup."
            )
    return build_provider_setup(
        ProviderSetupRequest(
            preset_id=preset.preset_id,
            agent_id=agent_name,
            model=model,
            base_url=base_url,
            stored_api_key=stored_api_key,
            allow_local_api_key=allow_local,
            config_path=getattr(args, "config", None),
            home_root=roots.home_root,
            data_root=roots.data_root,
            env=env_snapshot,
        )
    )


def _build_non_interactive_setup(
    args, *, roots, agent_name: str
) -> ProviderSetupResult:
    preset_id = str(getattr(args, "provider", "") or "").strip()
    api_format = str(getattr(args, "api_format", "") or "").strip()
    custom_preset_by_format = {
        "openai-compatible": "custom-openai-compatible",
        "anthropic-compatible": "custom-anthropic-compatible",
    }
    if api_format:
        expected_preset = custom_preset_by_format.get(api_format)
        if expected_preset is None:
            raise ProviderSetupError(f"Unsupported API format {api_format!r}.")
        if preset_id != expected_preset:
            raise ProviderSetupError(
                f"--api-format {api_format!r} requires --provider {expected_preset!r}."
            )
    try:
        return build_provider_setup(
            ProviderSetupRequest(
                preset_id=preset_id,
                agent_id=agent_name,
                model=str(getattr(args, "model", "") or "").strip(),
                base_url=str(getattr(args, "base_url", "") or "").strip(),
                config_path=getattr(args, "config", None),
                home_root=roots.home_root,
                data_root=roots.data_root,
                env=resolve_environment_config().snapshot(),
            )
        )
    except SetupCatalogError as exc:
        raise ProviderSetupError(str(exc)) from exc


def _print_preview(result: ProviderSetupResult) -> None:
    print("Setup preview:")
    for line in result.preview.lines():
        print(f"  {line}")


def _prompt_provider_check(preset: ProviderSetupPreset) -> bool:
    if preset.is_local:
        return False
    print("Optional provider check: sends one low-token request and may consume quota.")
    return _prompt_confirm("Run provider check after doctor?", default=False)


def _run_setup_doctor(*, config_path: Path) -> int:
    from openminion.cli.commands.doctor import run_doctor

    return _run_doctor_quietly(
        run_doctor,
        SimpleNamespace(
            config=str(config_path),
            check_turn=False,
            message="onboarding doctor",
            target="onboarding-setup",
            channel=None,
            json=False,
            skip_supervision=True,
            summary_only=True,
        ),
    )


def _run_setup_provider_check(*, config_path: Path) -> int:
    from openminion.cli.commands.doctor import run_doctor

    return _run_doctor_quietly(
        run_doctor,
        SimpleNamespace(
            config=str(config_path),
            check_turn=True,
            message="Reply with exactly: openminion provider check ok",
            target="onboarding-provider-check",
            channel=None,
            json=False,
            skip_supervision=True,
            summary_only=True,
        ),
    )


def _run_doctor_quietly(run_doctor, args) -> int:
    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        return int(run_doctor(args) or 0)
    finally:
        logging.disable(previous_disable_level)


def _launch_post_setup_interactive(args, *, config_path: Path) -> int:
    from openminion.cli.commands.interactive import run_interactive

    interactive_args = SimpleNamespace(
        config=str(config_path),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
        agent=getattr(args, "agent", None),
        session="onboarding-first-run",
        dir=str(Path.cwd()),
        theme=None,
        no_interactive=False,
        no_context=False,
        no_update_check=False,
        rich=False,
    )
    return int(run_interactive(interactive_args) or 0)


_launch_post_setup_focus = _launch_post_setup_interactive


def _resolve_runtime_helper(name: str) -> Any:
    module = importlib.import_module("openminion.cli.commands.setup")
    return getattr(module, name)


def run_setup(args) -> int:
    from openminion.base.config.core import resolve_default_agent_id

    try:
        config, saved_path = _resolve_runtime_helper("_run_wizard")(args)
    except ProviderSetupError as exc:
        print(f"Setup failed: {exc}")
        return 2
    if config.runtime.demo_mode:
        mode = "demo"
    else:
        _default_agent_id = resolve_default_agent_id(config)
        mode = config.agents[_default_agent_id].provider
    print(f"Initialized onboarding config at {saved_path} (mode: {mode})")

    doctor_code = _resolve_runtime_helper("_run_setup_doctor")(config_path=saved_path)
    if doctor_code != 0:
        print(
            "Setup validation failed. Fix the reported issues and rerun `openminion setup`."
        )
        return doctor_code

    provider_check_requested = bool(
        getattr(args, "check_provider", False)
        or getattr(args, "_provider_check_authorized", False)
    )
    if provider_check_requested:
        check_code = _resolve_runtime_helper("_run_setup_provider_check")(
            config_path=saved_path
        )
        if check_code != 0:
            print(
                "Provider check failed; config was written but readiness is not claimed."
            )
            return check_code
    else:
        print("Provider check skipped; no remote provider request was made.")

    if getattr(args, "no_chat", False):
        print(
            "Setup complete. Interactive launch skipped because "
            "--no-chat/--no-focus was requested."
        )
        return 0

    print("Setup validation passed. Entering OpenMinion...")
    return _resolve_runtime_helper("_launch_post_setup_focus")(
        args, config_path=saved_path
    )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    setup = subparsers.add_parser(
        "setup",
        help="Interactive first-run setup and validation",
    )
    setup.add_argument(
        "--no-chat",
        "--no-focus",
        dest="no_chat",
        action="store_true",
        help="Configure and validate only; do not launch the interactive CLI afterward",
    )
    setup.add_argument(
        "--agent",
        default=None,
        help="Agent id to configure for the first interactive session",
    )
    setup.add_argument(
        "--provider",
        default=None,
        help="Non-interactive provider preset id, for example openai or minimax",
    )
    setup.add_argument(
        "--model",
        default=None,
        help="Non-interactive model id or interactive default model override",
    )
    setup.add_argument(
        "--base-url",
        default=None,
        help="Base URL for custom provider presets that require one",
    )
    setup.add_argument(
        "--api-format",
        choices=("openai-compatible", "anthropic-compatible"),
        default=None,
        help="Validate the API format of a matching custom provider preset",
    )
    setup.add_argument(
        "--check-provider",
        action="store_true",
        help="Authorize one bounded provider validation request after setup",
    )
    setup.set_defaults(handler=run_setup, needs_app=False)
