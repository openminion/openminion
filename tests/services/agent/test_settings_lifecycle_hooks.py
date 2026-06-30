from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path

from openminion.base.config.settings import (
    SETTINGS_DIRNAME,
    SETTINGS_FILENAME,
    SettingsResolver,
)
from openminion.services.agent.hooks import HookContext
from openminion.services.agent.lifecycle import (
    LIFECYCLE_EVENT_POST_TOOL_USE,
    LIFECYCLE_EVENT_PRE_TOOL_USE,
    LifecycleEvent,
    LifecycleHookRegistry,
    register_settings_lifecycle_hooks,
    reset_default_lifecycle_registry,
)


def teardown_function() -> None:
    reset_default_lifecycle_registry()


def _write_settings(workspace: Path, payload: object) -> None:
    path = workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _hook_context() -> HookContext:
    return HookContext(config=None, logger=logging.getLogger("test.settings_hooks"))  # type: ignore[arg-type]


def test_settings_lifecycle_hook_runs_matching_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = tmp_path / "hook.txt"
    _write_settings(
        workspace,
        {
            "hooks": {
                "pre_tool_use": [
                    {
                        "matcher": "exec.*",
                        "command": (
                            'printf "%s" "$TOOL_NAME:$TOOL_OK:$SESSION_ID" > '
                            f"{shlex.quote(str(output))}"
                        ),
                    }
                ]
            }
        },
    )
    registry = LifecycleHookRegistry()
    resolver = SettingsResolver(workspace_root=workspace, user_home=tmp_path / "user")

    assert register_settings_lifecycle_hooks(resolver, registry=registry) == 1
    registry.fire(
        LifecycleEvent(
            event_type=LIFECYCLE_EVENT_PRE_TOOL_USE,
            session_id="session-1",
            tool_name="exec.run",
            tool_ok=True,
        ),
        _hook_context(),
    )

    assert output.read_text(encoding="utf-8") == "exec.run:1:session-1"


def test_settings_lifecycle_hook_matcher_filters_tool_name(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = tmp_path / "hook.txt"
    _write_settings(
        workspace,
        {
            "hooks": {
                "post_tool_use": [
                    {
                        "matcher": "exec.*",
                        "command": f"touch {shlex.quote(str(output))}",
                    }
                ]
            }
        },
    )
    registry = LifecycleHookRegistry()
    resolver = SettingsResolver(workspace_root=workspace, user_home=tmp_path / "user")
    register_settings_lifecycle_hooks(resolver, registry=registry)

    registry.fire(
        LifecycleEvent(
            event_type=LIFECYCLE_EVENT_POST_TOOL_USE,
            tool_name="file.read",
        ),
        _hook_context(),
    )

    assert not output.exists()


def test_settings_lifecycle_hook_nonzero_exit_is_observe_only(
    tmp_path: Path,
    caplog,
) -> None:
    workspace = tmp_path / "workspace"
    _write_settings(
        workspace,
        {"hooks": {"pre_tool_use": [{"command": "exit 7"}]}},
    )
    registry = LifecycleHookRegistry()
    resolver = SettingsResolver(workspace_root=workspace, user_home=tmp_path / "user")
    register_settings_lifecycle_hooks(resolver, registry=registry)

    with caplog.at_level(logging.WARNING, logger="test.settings_hooks"):
        registry.fire(
            LifecycleEvent(
                event_type=LIFECYCLE_EVENT_PRE_TOOL_USE,
                tool_name="exec.run",
            ),
            _hook_context(),
        )

    assert "settings lifecycle hook exited non-zero" in caplog.text


def test_settings_lifecycle_hooks_absent_settings_registers_nothing(
    tmp_path: Path,
) -> None:
    registry = LifecycleHookRegistry()
    resolver = SettingsResolver(
        workspace_root=tmp_path / "workspace",
        user_home=tmp_path / "user",
    )

    assert register_settings_lifecycle_hooks(resolver, registry=registry) == 0
    assert registry.count() == 0
