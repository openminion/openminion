from __future__ import annotations

import importlib

from openminion.modules.brain.bootstrap.route_catalog import registered_routes


_HIDDEN_MODE_MODULES = (
    # act/loop.py (formerly loop.single + loop.series + loop.adaptive consolidated)
    "openminion.modules.brain.loop.adaptive",
    "openminion.modules.brain.loop.strategies.coding.handler",
    "openminion.modules.brain.loop.strategies.research.handler",
    "openminion.modules.brain.execution.orchestrate.handler",
    "openminion.modules.brain.execution.targets.delegated.handler",
    "openminion.modules.brain.loop.tools.phases.observe",
    "openminion.modules.brain.loop.tools.phases.eval",
    "openminion.modules.brain.loop.tools.phases.refine",
)

_HIDDEN_MODE_NAMES = (
    "act_loop",
    "act_loop_adaptive",
    "act_profile_coding",
    "act_profile_research",
    "act_profile_orchestrate",
    "execution_target_delegated",
    "loop_phase_observe",
    "loop_phase_eval",
    "loop_phase_refine",
)


def test_default_boot_registry_only_exposes_public_modes() -> None:
    assert registered_routes() == ["act", "respond"]


def test_force_importing_hidden_handler_modules_does_not_expand_registry() -> None:
    for module_name in _HIDDEN_MODE_MODULES:
        importlib.import_module(module_name)

    assert registered_routes() == ["act", "respond"]
    for hidden_mode_name in _HIDDEN_MODE_NAMES:
        assert hidden_mode_name not in registered_routes()
