from __future__ import annotations

import importlib

import pytest

import openminion.api.runtime  # noqa: F401
import openminion.api.server  # noqa: F401


@pytest.mark.parametrize(
    ("module_name", "symbol_name"),
    [
        ("openminion.services.bootstrap.config", "bootstrap_config_manager"),
        ("openminion.services.bootstrap.migration", "migrate_data_root"),
        ("openminion.services.diagnostics.debug", "is_debug_surface_enabled"),
        ("openminion.services.bootstrap.onboarding", "OnboardingAction"),
        ("openminion.services.diagnostics.owner_status", "build_owner_status"),
        ("openminion.services.bootstrap.paths", "SERVICES_STATE_DIRNAME"),
        ("openminion.services.lifecycle.request_orchestrator", "run_turn"),
        ("openminion.services.lifecycle.self_improvement", "SelfImprovementEngine"),
        ("openminion.services.lifecycle.sidecars", "SidecarManager"),
        ("openminion.modules.skill.diagnostics.harness", "run_skill_harness"),
        ("openminion.modules.storage.runtime.vector_sync", "VectorSyncScheduler"),
    ],
)
def test_moving_services_modules_expose_expected_symbol(
    module_name: str,
    symbol_name: str,
) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, symbol_name), f"{module_name} missing {symbol_name}"
