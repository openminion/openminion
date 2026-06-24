from __future__ import annotations

from openminion.modules.tool.contracts.model_ids import (
    MODEL_MEMORY_FORGET,
    MODEL_MEMORY_SEARCH,
    MODEL_MEMORY_WRITE,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_MEMORY_FORGET,
    RUNTIME_MEMORY_SEARCH,
    RUNTIME_MEMORY_WRITE,
)
from openminion.modules.tool.runtime.registrar import (
    ToolModuleRegistrar,
    ToolRegisterContext,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.memory import REGISTRAR


def test_memory_registrar_conforms_to_protocol() -> None:
    assert isinstance(REGISTRAR, ToolModuleRegistrar)
    assert REGISTRAR.module_id == "memory"
    assert REGISTRAR.is_provider_only is False


def test_memory_manifest_matches_contract_ids_and_candidates() -> None:
    manifest = REGISTRAR.get_manifest(ToolRegisterContext(module_id="memory"))

    model_ids = [item.model_tool_id for item in manifest.model_tools]
    assert model_ids == [
        MODEL_MEMORY_WRITE,
        MODEL_MEMORY_SEARCH,
        MODEL_MEMORY_FORGET,
    ]

    binding_map = {
        item.runtime_binding_id: (item.model_tool_id, tuple(item.runtime_candidates))
        for item in manifest.runtime_bindings
    }
    assert binding_map[RUNTIME_MEMORY_WRITE] == (
        MODEL_MEMORY_WRITE,
        ("memory.write",),
    )
    assert binding_map[RUNTIME_MEMORY_SEARCH] == (
        MODEL_MEMORY_SEARCH,
        ("memory.search",),
    )
    assert binding_map[RUNTIME_MEMORY_FORGET] == (
        MODEL_MEMORY_FORGET,
        ("memory.forget",),
    )


def test_memory_registrar_registers_runtime_candidates() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry, ToolRegisterContext(module_id="memory"))
    names = set(registry.list().keys())
    assert "memory.write" in names
    assert "memory.search" in names
    assert "memory.forget" in names
