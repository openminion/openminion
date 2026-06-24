from __future__ import annotations

import json

from .cli_entrypoint_paths import (
    has_module_cli_entrypoint,
    module_cli_entrypoint_path,
    module_cli_fixture_path,
    openminion_modules_root,
)


def test_non_exempt_modules_have_debug_command_map_with_json_signals() -> None:
    modules_root = openminion_modules_root()

    exemptions = json.loads(
        module_cli_fixture_path("cli_exemptions.json").read_text(encoding="utf-8")
    )
    exempt_modules = {entry["module"] for entry in exemptions["exemptions"]}
    non_exempt_modules = {
        p.name
        for p in modules_root.iterdir()
        if p.is_dir()
        and (p / "__init__.py").exists()
        and has_module_cli_entrypoint(p)
        and p.name not in exempt_modules
    }

    debug_map = json.loads(
        module_cli_fixture_path("cli_debug_command_map.json").read_text(
            encoding="utf-8"
        )
    )
    module_entries = debug_map.get("modules")
    assert isinstance(module_entries, dict)
    assert set(module_entries.keys()) == non_exempt_modules

    for module_name, entry in module_entries.items():
        assert isinstance(entry, dict)
        debug_command = entry.get("debug_command")
        json_signals = entry.get("json_signals")
        assert isinstance(debug_command, list) and debug_command, (
            f"{module_name}: missing debug_command"
        )
        assert all(isinstance(arg, str) and arg.strip() for arg in debug_command), (
            f"{module_name}: invalid debug_command args"
        )
        assert isinstance(json_signals, list) and json_signals, (
            f"{module_name}: missing json_signals"
        )
        assert all(
            isinstance(signal, str) and signal.strip() for signal in json_signals
        ), f"{module_name}: invalid json_signals"

        cli_path = module_cli_entrypoint_path(modules_root / module_name)
        assert cli_path is not None
        cli_source = cli_path.read_text(encoding="utf-8")
        assert any(signal in cli_source for signal in json_signals), (
            f"{module_name}: none of json_signals found in CLI entrypoint"
        )
