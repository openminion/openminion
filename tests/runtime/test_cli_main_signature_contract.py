from __future__ import annotations

import inspect

from openminion.modules.brain.cli import main as brain_main
from openminion.modules.llm.cli import main as llm_main
from openminion.modules.registry.cli import main as registry_main
from openminion.modules.tool.cli import main as tool_main


def _assert_main_contract(func) -> None:
    sig = inspect.signature(func)
    assert "argv" in sig.parameters
    argv_param = sig.parameters["argv"]
    assert argv_param.default is None
    assert func(["--help"]) == 0


def test_brain_llm_registry_tool_main_contract() -> None:
    for func in (brain_main, llm_main, registry_main, tool_main):
        _assert_main_contract(func)
