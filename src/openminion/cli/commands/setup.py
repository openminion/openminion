from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.base.config import AgentProfileConfig, OpenMinionConfig, save_config
from openminion.cli.config import resolve_cli_roots


def _install_default_agent(
    config: OpenMinionConfig,
    *,
    agent_id: str,
    provider: str,
) -> None:
    config.agents = {
        agent_id: AgentProfileConfig(name=agent_id, provider=provider),
    }


_CLOUD_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "openai": ("OPENAI_API_KEY", "gpt-4.1-mini"),
    "anthropic": ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-latest"),
    "openrouter": ("OPENROUTER_API_KEY", "openai/gpt-4.1-mini"),
}


@dataclass(frozen=True)
class SetupSelection:
    track: str
    provider: str


def _prompt_choice(prompt: str, options: dict[str, SetupSelection]) -> SetupSelection:
    while True:
        print(prompt)
        for key, selection in options.items():
            label = selection.track.replace("_", " ").title()
            if selection.track == "cloud":
                label = f"{label} ({selection.provider})"
            print(f"  {key}. {label}")
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
    value = str(input(f"{prompt}{suffix}: ") or "").strip()
    return value or default


def _default_storage_path(data_root: Path) -> str:
    return str((data_root / "state" / "openminion.db").resolve(strict=False))


def _build_cloud_config(
    *,
    provider: str,
    api_key: str,
    agent_name: str,
    data_root: Path,
) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _install_default_agent(config, agent_id=agent_name, provider=provider)
    config.runtime.demo_mode = False
    config.storage.path = _default_storage_path(data_root)
    env_name, model = _CLOUD_PROVIDER_DEFAULTS[provider]
    provider_cfg = getattr(config.providers, provider)
    provider_cfg.api_key_env = env_name
    provider_cfg.model = model
    provider_cfg.api_key = api_key
    return config


def _build_ollama_config(
    *,
    agent_name: str,
    data_root: Path,
) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _install_default_agent(config, agent_id=agent_name, provider="ollama")
    config.runtime.demo_mode = False
    config.storage.path = _default_storage_path(data_root)
    config.providers.ollama.model = _prompt_text("Ollama model", default="llama3.1")
    config.providers.ollama.base_url = _prompt_text(
        "Ollama base URL",
        default="http://127.0.0.1:11434",
    )
    return config


def _build_demo_config(*, agent_name: str, data_root: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _install_default_agent(config, agent_id=agent_name, provider="echo")
    config.runtime.demo_mode = True
    config.storage.path = _default_storage_path(data_root)
    return config


def _run_wizard(args) -> tuple[OpenMinionConfig, Path]:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    agent_name = str(getattr(args, "agent", "") or "").strip() or "openminion"

    selection = _prompt_choice(
        "Choose your setup path:",
        {
            "1": SetupSelection(track="cloud", provider="openai"),
            "2": SetupSelection(track="local setup with ollama", provider="ollama"),
            "3": SetupSelection(track="demo mode", provider="echo"),
        },
    )

    if selection.provider == "echo":
        config = _build_demo_config(agent_name=agent_name, data_root=roots.data_root)
    elif selection.provider == "ollama":
        config = _build_ollama_config(agent_name=agent_name, data_root=roots.data_root)
    else:
        provider = _prompt_choice(
            "Choose your cloud provider:",
            {
                "1": SetupSelection(track="cloud", provider="openai"),
                "2": SetupSelection(track="cloud", provider="anthropic"),
                "3": SetupSelection(track="cloud", provider="openrouter"),
            },
        ).provider
        env_name, _ = _CLOUD_PROVIDER_DEFAULTS[provider]
        print(
            f"Stored as a convenience in the config file. You can override any time by setting {env_name}."
        )
        print(f"If {env_name} is set, it always wins over the config file value.")
        api_key = _prompt_text(
            f"{provider} API key (optional convenience; leave blank to use {env_name} from env later)"
        )
        config = _build_cloud_config(
            provider=provider,
            api_key=api_key,
            agent_name=agent_name,
            data_root=roots.data_root,
        )

    saved_path = save_config(
        config,
        getattr(args, "config", None),
        home_root=roots.home_root,
    )
    return config, saved_path


def _run_setup_doctor(*, config_path: Path) -> int:
    from openminion.cli.commands.doctor import run_doctor

    return int(
        run_doctor(
            SimpleNamespace(
                config=str(config_path),
                check_turn=False,
                message="onboarding doctor",
                target="onboarding-setup",
                channel=None,
                json=False,
                skip_supervision=True,
            )
        )
        or 0
    )


def _launch_post_setup_focus(args, *, config_path: Path) -> int:
    from openminion.cli.commands.focus import run_focus

    focus_args = SimpleNamespace(
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
        rich=True,
        terminal=False,
    )
    return int(run_focus(focus_args) or 0)


def _resolve_runtime_helper(name: str) -> Any:
    module = importlib.import_module("openminion.cli.commands.setup")
    return getattr(module, name)


def _print_post_setup_tips() -> None:
    paragraphs = (
        "Tip: run `openminion` to start a focus shell that flows in "
        "your terminal (scroll up to re-read past turns). Use "
        "`openminion focus --rich` for the Textual shell with the "
        "full overlay set, or pipe a prompt via "
        "`cat prompt.md | openminion` for one-shot mode.",
        "Each turn renders with a `⏺` marker, a verb-rotating "
        "thinking spinner, colored `●` tool-call markers, and "
        "syntax-highlighted code blocks. Use `--progress minimal` "
        "or `--progress off` for reduced motion; `--plain-spinner`, "
        "`OPENMINION_FOCUS_PLAIN_SPINNER=1`, and `NO_COLOR=1` "
        "remain compatibility paths. "
        "Tool blocks longer than 6 lines are truncated; type "
        "`/expand` to re-print the most recent one in full "
        "(`/expand 2` for the second-most-recent, `/expand 0` for a "
        "list).",
        "Activity animation defaults to `openminion:braille`. Install "
        "`openminion[animations]` to enable `unicode-animatio`, then "
        "launch with `--animation-provider unicode --animation helix` "
        "or switch live with `/animation use unicode:helix`.",
        "Tool-block verbosity is configurable: `--verbosity quiet` "
        "hides tool blocks (an end-of-turn summary still shows "
        "what ran); `--verbosity normal` is the default; "
        "`--verbosity verbose` shows full tool bodies up to a "
        "200-line cap. Same effect via "
        "`OPENMINION_FOCUS_VERBOSITY=quiet|normal|verbose`. "
        "Toggle live with `/quiet`, `/verbose`, `/normal` slash "
        "commands. Failed tool calls show `✗ (exit N)` in red.",
        "Edit and Write tool calls render with inline unified-diff "
        "coloring (`+` lines green, `-` lines red, `@@` hunk "
        "headers cyan). The same verbosity ladder applies — "
        "`/expand` shows the full diff, `--verbosity verbose` "
        "shows up to 200 lines inline.",
        "Persistent preferences: create "
        "`<DATA_ROOT>/focus_prefs.toml` with flat keys "
        '`verbosity = "quiet"`, `progress = "off"`, '
        '`animation_provider = "unicode"`, and/or `animation = "helix"` to '
        "set per-user defaults. Slash overrides + CLI flag + env "
        "vars still win when set.",
        "Tool calls narrate live: a yellow `●` marker prints "
        "`Running Bash(...)` when a tool starts; the final block "
        "(with output + exit code + diff coloring for Edit/Write) "
        "prints on completion. Quiet mode hides both but still "
        "shows the end-of-turn hidden-count summary.",
        "Project context: drop `OPENMINION.md` (or `AGENTS.md` / "
        "`CLAUDE.md`) at your project root; the focus shell loads "
        "it at startup so the agent has project-specific context "
        "every session. Bootstrap one with `/init`.",
        "Switch models mid-session with `/model <provider>` (e.g. "
        "`/model anthropic` or `/model openai/gpt-4o`); compact "
        "long conversations with `/compact`; list MCP servers + "
        "health with `/mcp`; toggle read-only mode with "
        "`/readonly`. All session-scoped — restart reverts.",
        "Custom slash commands: drop Markdown files in "
        "`.openminion/commands/*.md` (project) or "
        "`<DATA_ROOT>/commands/*.md` (user-global). Frontmatter "
        "supports `description`, `model`, `agent`; body supports "
        "`$ARGUMENTS`, `$1..$N`, `@file`, `!`cmd`` interpolation.",
        "The `--verbosity` and `--progress` flags work uniformly "
        "across `openminion`, `gateway run`, `openminion run`, "
        "and `openminion agent` (CUC). Same env vars: "
        "`OPENMINION_VERBOSITY` and `OPENMINION_PROGRESS`. "
        "Piped contexts auto-detect to `--progress off`.",
        "`openminion chat` is a compatibility alias. Use bare `openminion` "
        "or `openminion focus` for interactive work, and `openminion run` "
        "for scripted one-shot execution.",
    )
    for paragraph in paragraphs:
        print(paragraph)


def run_setup(args) -> int:
    from openminion.base.config.core import resolve_default_agent_id

    config, saved_path = _resolve_runtime_helper("_run_wizard")(args)
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

    if getattr(args, "no_chat", False):
        print(
            "Setup complete. Interactive launch skipped because "
            "--no-chat/--no-focus was requested."
        )
        _print_post_setup_tips()
        return 0

    print("Setup validation passed. Entering Focus...")
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
        help="Configure and validate only; do not launch Focus afterwards",
    )
    setup.add_argument(
        "--agent",
        default=None,
        help="Agent id to configure for the first interactive session",
    )
    setup.set_defaults(handler=run_setup, needs_app=False)
