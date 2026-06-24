from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_FILE_EDIT,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_READ_RANGE,
    MODEL_FILE_SEARCH,
    MODEL_FILE_TRASH,
    MODEL_FILE_WRITE,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_FILE_EDIT,
    RUNTIME_FILE_FIND,
    RUNTIME_FILE_LIST_DIR,
    RUNTIME_FILE_READ,
    RUNTIME_FILE_READ_RANGE,
    RUNTIME_FILE_SEARCH,
    RUNTIME_FILE_TRASH,
    RUNTIME_FILE_WRITE,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


FILE_MODEL_TOOLS = (
    ModelToolDef(
        model_tool_id=MODEL_FILE_LIST_DIR,
        description="List directory contents",
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_READ,
        description="Read file contents",
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_READ_RANGE,
        description="Read a line-numbered inclusive range from a file",
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_WRITE,
        description=(
            "Write or overwrite a file, creating parent directories "
            "automatically for new project scaffolding."
        ),
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_FIND,
        description="Find files by name pattern",
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_TRASH,
        description="Move file to trash",
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_SEARCH,
        description="Search file contents by query or regex",
        parameters={},
    ),
    ModelToolDef(
        model_tool_id=MODEL_FILE_EDIT,
        description="Apply structured exact-anchor edits to a file",
        parameters={},
    ),
)

FILE_RUNTIME_BINDINGS = (
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_LIST_DIR,
        model_tool_id=MODEL_FILE_LIST_DIR,
        runtime_candidates=("file.list_dir",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_READ,
        model_tool_id=MODEL_FILE_READ,
        runtime_candidates=("file.read",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_READ_RANGE,
        model_tool_id=MODEL_FILE_READ_RANGE,
        runtime_candidates=("file.read_range",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_WRITE,
        model_tool_id=MODEL_FILE_WRITE,
        runtime_candidates=("file.write",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_FIND,
        model_tool_id=MODEL_FILE_FIND,
        runtime_candidates=("file.find",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_TRASH,
        model_tool_id=MODEL_FILE_TRASH,
        runtime_candidates=("file.trash",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_SEARCH,
        model_tool_id=MODEL_FILE_SEARCH,
        runtime_candidates=("file.search",),
    ),
    RuntimeBindingDef(
        runtime_binding_id=RUNTIME_FILE_EDIT,
        model_tool_id=MODEL_FILE_EDIT,
        runtime_candidates=("file.edit",),
    ),
)


class FileRegistrar:
    """Registrar with manifest for file tool module."""

    module_id = "file"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register file tools with runtime registry."""
        from .plugin import register as tool_register

        tool_register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return ToolBindingManifest for file module."""
        return ToolBindingManifest(
            module_id="file",
            model_tools=FILE_MODEL_TOOLS,
            runtime_bindings=FILE_RUNTIME_BINDINGS,
        )


REGISTRAR = FileRegistrar()
