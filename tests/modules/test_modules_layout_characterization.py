from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("module_name", "symbol_name"),
    [
        ("openminion.modules.a2a", "__path__"),
        ("openminion.modules.artifact", "__path__"),
        ("openminion.modules.brain", "__path__"),
        ("openminion.modules.context", "__path__"),
        ("openminion.modules.controlplane", "__path__"),
        ("openminion.modules.identity", "__path__"),
        ("openminion.modules.llm", "__path__"),
        ("openminion.modules.memory", "__path__"),
        ("openminion.modules.policy", "__path__"),
        ("openminion.modules.retrieve", "__path__"),
        ("openminion.modules.secret", "__path__"),
        ("openminion.modules.session", "__path__"),
        ("openminion.modules.skill", "__path__"),
        ("openminion.modules.storage", "__path__"),
        ("openminion.modules.task", "__path__"),
        ("openminion.modules.telemetry", "__path__"),
        ("openminion.modules.tool", "__path__"),
        ("openminion.modules.base", "ModuleBase"),
        ("openminion.modules.cli_common", "apply_home_data_root_env"),
        ("openminion.modules.config", "resolve_module_home_root"),
        ("openminion.modules.providers", "ModuleRegistry"),
        ("openminion.modules.paths", "SESSION_DB_SUBPATH"),
    ],
)
def test_modules_layout_characterization_import_surface(
    module_name: str,
    symbol_name: str,
) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, symbol_name), f"{module_name} missing {symbol_name}"
