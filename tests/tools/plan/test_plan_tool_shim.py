from __future__ import annotations

import importlib
import sys
import warnings
from types import SimpleNamespace

from openminion.tools.todo import TODO_FAMILY


def _clear_tool_shim_modules() -> None:
    for name in list(sys.modules):
        if name == "openminion.tools.plan" or name.startswith("openminion.tools.plan."):
            sys.modules.pop(name, None)


def test_plan_tool_package_shim_warns_and_reexports_family() -> None:
    _clear_tool_shim_modules()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        shim = importlib.import_module("openminion.tools.plan")
    assert shim.PLAN_FAMILY is TODO_FAMILY
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) == 1


def test_plan_tool_plugin_shim_exposes_legacy_handlers() -> None:
    _clear_tool_shim_modules()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        plugin = importlib.import_module("openminion.tools.plan.plugin")
    plugin._reset_store_for_tests()
    result = plugin._h_set({"items": ["shim item"]}, SimpleNamespace(session_id="shim"))
    assert result["plan"]["items"][0]["text"] == "shim item"
