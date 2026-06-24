from __future__ import annotations

import json
import logging
from pathlib import Path

from openminion.base.config.settings import (
    LOCAL_SETTINGS_FILENAME,
    SETTINGS_DIRNAME,
    SETTINGS_FILENAME,
    SettingsResolver,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_settings_resolver_loads_three_tiers_with_local_precedence(
    tmp_path: Path,
) -> None:
    user_home = tmp_path / "user"
    workspace = tmp_path / "workspace"
    _write_json(
        user_home / SETTINGS_DIRNAME / SETTINGS_FILENAME,
        {
            "theme": "dark",
            "hooks": {"pre_tool_use": [{"command": "user"}]},
            "nested": {"a": 1, "b": 1},
        },
    )
    _write_json(
        workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME,
        {
            "theme": "light",
            "hooks": {"post_tool_use": [{"command": "project"}]},
            "nested": {"b": 2},
        },
    )
    _write_json(
        workspace / SETTINGS_DIRNAME / LOCAL_SETTINGS_FILENAME,
        {
            "theme": "solarized",
            "nested": {"c": 3},
        },
    )

    settings = SettingsResolver(workspace_root=workspace, user_home=user_home).load()

    assert settings["theme"] == "solarized"
    assert settings["nested"] == {"a": 1, "b": 2, "c": 3}
    assert settings["hooks"] == {
        "pre_tool_use": [{"command": "user"}],
        "post_tool_use": [{"command": "project"}],
    }


def test_settings_resolver_missing_files_default_empty(tmp_path: Path) -> None:
    resolver = SettingsResolver(
        workspace_root=tmp_path / "workspace",
        user_home=tmp_path / "user",
    )

    assert resolver.load() == {}


def test_settings_resolver_skips_malformed_json(
    tmp_path: Path,
    caplog,
) -> None:
    user_home = tmp_path / "user"
    workspace = tmp_path / "workspace"
    bad = user_home / SETTINGS_DIRNAME / SETTINGS_FILENAME
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json", encoding="utf-8")
    _write_json(workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME, {"ok": True})

    with caplog.at_level(logging.WARNING):
        settings = SettingsResolver(
            workspace_root=workspace,
            user_home=user_home,
        ).load()

    assert settings == {"ok": True}
    assert "Skipping malformed settings file" in caplog.text


def test_settings_resolver_skips_non_object_json(
    tmp_path: Path,
    caplog,
) -> None:
    user_home = tmp_path / "user"
    workspace = tmp_path / "workspace"
    _write_json(user_home / SETTINGS_DIRNAME / SETTINGS_FILENAME, ["nope"])
    _write_json(workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME, {"ok": True})

    with caplog.at_level(logging.WARNING):
        settings = SettingsResolver(
            workspace_root=workspace,
            user_home=user_home,
        ).load()

    assert settings == {"ok": True}
    assert "Skipping non-object settings file" in caplog.text


def test_settings_resolver_caches_until_reload(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    workspace = tmp_path / "workspace"
    path = workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME
    _write_json(path, {"value": 1})
    resolver = SettingsResolver(workspace_root=workspace, user_home=user_home)

    assert resolver.load() == {"value": 1}
    _write_json(path, {"value": 2})
    assert resolver.load() == {"value": 1}
    assert resolver.reload() == {"value": 2}


def test_lifecycle_hooks_for_event_normalizes_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_json(
        workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME,
        {
            "hooks": {
                "pre_tool_use": [
                    {"command": "echo $TOOL_NAME", "matcher": "exec.*"},
                    {"command": "  "},
                    "bad",
                ],
                "post_tool_use": {"bad": True},
            }
        },
    )
    resolver = SettingsResolver(workspace_root=workspace, user_home=tmp_path / "user")

    assert resolver.lifecycle_hooks_for_event("pre_tool_use") == [
        {"command": "echo $TOOL_NAME", "matcher": "exec.*"}
    ]
    assert resolver.lifecycle_hooks_for_event("post_tool_use") == []
    assert resolver.lifecycle_hooks_for_event("missing") == []
