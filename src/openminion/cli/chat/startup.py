from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from openminion.base.config import AgentProfileConfig, OpenMinionConfig
from openminion.services.bootstrap.onboarding import (
    OnboardingRequestedMode,
    build_inline_setup_args,
    format_fail_fast_message,
    resolve_surface_onboarding_route,
)


def resolve_chat_roots(
    args: Any,
    *,
    resolve_cli_roots: Callable[..., Any],
    resolve_config_path: Callable[..., Path],
) -> tuple[Path, object]:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=roots.home_root,
    )
    return config_path, roots


def print_onboarding_fail_fast(status: Any) -> int:
    print(format_fail_fast_message(surface="openminion chat", status=status))
    return 2


def inspect_chat_onboarding(
    args: Any,
    *,
    resolve_chat_roots_fn: Callable[[Any], tuple[Path, object]],
    has_tty_fn: Callable[[], bool],
) -> tuple[Any, Path, object]:
    config_path, roots = resolve_chat_roots_fn(args)
    route = resolve_surface_onboarding_route(
        config_path=config_path,
        home_root=roots.home_root,
        data_root=roots.data_root,
        config_arg=getattr(args, "config", None),
        agent_id=str(getattr(args, "agent", "") or "").strip() or None,
        requested_mode=(
            OnboardingRequestedMode.DEMO
            if bool(getattr(args, "demo", False))
            else OnboardingRequestedMode.AUTO
        ),
        has_tty=has_tty_fn(),
        no_interactive=bool(getattr(args, "no_interactive", False)),
        env=roots.env,
    )
    return route.status, config_path, roots


def build_demo_chat_config(*, agent_name: str, data_root: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    resolved_name = agent_name or "openminion"
    config.agents = {
        resolved_name: AgentProfileConfig(name=resolved_name, provider="echo"),
    }
    config.runtime.demo_mode = True
    config.storage.path = str((data_root / "state" / "openminion.db").resolve())
    return config


def materialize_demo_config_for_chat(
    args: Any,
    *,
    roots: Any,
    config_path: Path,
    build_demo_chat_config_fn: Callable[..., OpenMinionConfig],
    save_config_fn: Callable[..., Path | str],
) -> Path:
    demo_config = build_demo_chat_config_fn(
        agent_name=str(getattr(args, "agent", "") or "").strip() or "openminion",
        data_root=roots.data_root,
    )
    target_path = config_path
    if not str(getattr(args, "config", "") or "").strip():
        target_path = (
            roots.data_root / "onboarding" / "chat-demo-config.json"
        ).resolve(strict=False)
    saved = save_config_fn(
        demo_config,
        str(target_path),
        home_root=roots.home_root,
    )
    return Path(saved)


def run_inline_setup_for_chat(
    args: Any,
    *,
    run_setup_fn: Callable[[argparse.Namespace], int | None],
) -> int:
    return int(
        run_setup_fn(
            build_inline_setup_args(
                config=getattr(args, "config", None),
                home_root=getattr(args, "home_root", None),
                data_root=getattr(args, "data_root", None),
                no_chat=True,
                agent=getattr(args, "agent", None),
            )
        )
        or 0
    )
