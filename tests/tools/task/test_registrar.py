from __future__ import annotations

from openminion.modules.tool.contracts.model_ids import (
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_CANCEL,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TASK_WATCH,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_TASK_CONSOLIDATE_MEMORY,
    RUNTIME_TASK_CANCEL,
    RUNTIME_TASK_LIST,
    RUNTIME_TASK_PAUSE,
    RUNTIME_TASK_RESUME,
    RUNTIME_TASK_SCHEDULE,
    RUNTIME_TASK_SHOW,
    RUNTIME_TASK_WATCH,
)
from openminion.modules.tool.runtime.registrar import (
    ToolModuleRegistrar,
    ToolRegisterContext,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.task import REGISTRAR


def test_task_registrar_conforms_to_protocol() -> None:
    assert isinstance(REGISTRAR, ToolModuleRegistrar)
    assert REGISTRAR.module_id == "task"
    assert REGISTRAR.is_provider_only is False


def test_task_manifest_matches_contract_ids_and_candidates() -> None:
    manifest = REGISTRAR.get_manifest(ToolRegisterContext(module_id="task"))

    model_ids = [item.model_tool_id for item in manifest.model_tools]
    assert model_ids == [
        MODEL_TASK_SCHEDULE,
        MODEL_TASK_CONSOLIDATE_MEMORY,
        MODEL_TASK_WATCH,
        MODEL_TASK_CANCEL,
        MODEL_TASK_LIST,
        MODEL_TASK_PAUSE,
        MODEL_TASK_RESUME,
        MODEL_TASK_SHOW,
    ]

    binding_map = {
        item.runtime_binding_id: (item.model_tool_id, tuple(item.runtime_candidates))
        for item in manifest.runtime_bindings
    }
    assert binding_map[RUNTIME_TASK_SCHEDULE] == (
        MODEL_TASK_SCHEDULE,
        ("task.schedule",),
    )
    assert binding_map[RUNTIME_TASK_CONSOLIDATE_MEMORY] == (
        MODEL_TASK_CONSOLIDATE_MEMORY,
        ("task.consolidate_memory",),
    )
    assert binding_map[RUNTIME_TASK_WATCH] == (
        MODEL_TASK_WATCH,
        ("task.watch",),
    )
    assert binding_map[RUNTIME_TASK_CANCEL] == (
        MODEL_TASK_CANCEL,
        ("task.cancel",),
    )
    assert binding_map[RUNTIME_TASK_LIST] == (
        MODEL_TASK_LIST,
        ("task.list",),
    )
    assert binding_map[RUNTIME_TASK_PAUSE] == (
        MODEL_TASK_PAUSE,
        ("task.pause",),
    )
    assert binding_map[RUNTIME_TASK_RESUME] == (
        MODEL_TASK_RESUME,
        ("task.resume",),
    )
    assert binding_map[RUNTIME_TASK_SHOW] == (
        MODEL_TASK_SHOW,
        ("task.show",),
    )


def test_task_registrar_registers_runtime_candidates() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry, ToolRegisterContext(module_id="task"))
    names = set(registry.list().keys())
    assert "task.schedule" in names
    assert "task.consolidate_memory" in names
    assert "task.watch" in names
    assert "task.cancel" in names
    assert "task.list" in names
    assert "task.pause" in names
    assert "task.resume" in names
    assert "task.show" in names
    assert "task.stop" not in names
    assert "task.details" not in names
