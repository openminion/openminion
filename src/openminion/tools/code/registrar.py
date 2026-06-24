from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_GREP,
    MODEL_CODE_PATCH,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_SYMBOL_FIND,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_CODE_GREP,
    RUNTIME_CODE_PATCH,
    RUNTIME_CODE_REPO_INDEX,
    RUNTIME_CODE_REPO_MAP,
    RUNTIME_CODE_SYMBOL_FIND,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class CodeRegistrar:
    module_id = "code"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register as tool_register

        tool_register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_CODE_PATCH,
                    description="Apply a unified-diff patch to a file",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_CODE_GREP,
                    description="Search workspace text with rg and structured output",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_CODE_REPO_MAP,
                    description="Generate a compact repo map and symbol summary",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_CODE_REPO_INDEX,
                    description="Generate a structured repo index with files, symbols, and imports",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_CODE_SYMBOL_FIND,
                    description="Find symbol definitions and line ranges",
                    parameters={},
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_CODE_PATCH,
                    model_tool_id=MODEL_CODE_PATCH,
                    runtime_candidates=(MODEL_CODE_PATCH,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_CODE_GREP,
                    model_tool_id=MODEL_CODE_GREP,
                    runtime_candidates=(MODEL_CODE_GREP,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_CODE_REPO_MAP,
                    model_tool_id=MODEL_CODE_REPO_MAP,
                    runtime_candidates=(MODEL_CODE_REPO_MAP,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_CODE_REPO_INDEX,
                    model_tool_id=MODEL_CODE_REPO_INDEX,
                    runtime_candidates=(MODEL_CODE_REPO_INDEX,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_CODE_SYMBOL_FIND,
                    model_tool_id=MODEL_CODE_SYMBOL_FIND,
                    runtime_candidates=(MODEL_CODE_SYMBOL_FIND,),
                ),
            ),
        )


REGISTRAR = CodeRegistrar()


__all__ = ["CodeRegistrar", "REGISTRAR"]
