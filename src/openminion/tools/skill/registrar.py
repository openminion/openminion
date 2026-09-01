from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import (
    MODEL_SKILL_GET,
    MODEL_SKILL_INGEST,
    MODEL_SKILL_INGEST_URL,
    MODEL_SKILL_INSPECT,
    MODEL_SKILL_LIST,
    MODEL_SKILL_REMOVE,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_SKILL_GET,
    RUNTIME_SKILL_INGEST,
    RUNTIME_SKILL_INGEST_URL,
    RUNTIME_SKILL_INSPECT,
    RUNTIME_SKILL_LIST,
    RUNTIME_SKILL_REMOVE,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


_TOOL_DEFINITIONS = (
    (
        MODEL_SKILL_INGEST,
        "Ingest a skill definition (Markdown) and store it for reuse. Returns skill_id, version_hash, and a rendered snippet for immediate use.",
        RUNTIME_SKILL_INGEST,
    ),
    (
        MODEL_SKILL_INGEST_URL,
        "Fetch a remote markdown skill URL, safety-scan it, and store it for reuse in one step. Use this when the user wants to learn or ingest a skill directly from an http/https URL.",
        RUNTIME_SKILL_INGEST_URL,
    ),
    (
        MODEL_SKILL_INSPECT,
        "Inspect skill markdown for safety and risk issues before ingestion.",
        RUNTIME_SKILL_INSPECT,
    ),
    (
        MODEL_SKILL_LIST,
        "List stored skills with optional filters.",
        RUNTIME_SKILL_LIST,
    ),
    (
        MODEL_SKILL_GET,
        "Get one stored skill by ID and optional version hash. When the skill lists a bundled reference, asset, or script, call this tool again with resource_path and the same version_hash to read that resource before using it.",
        RUNTIME_SKILL_GET,
    ),
    (
        MODEL_SKILL_REMOVE,
        "Remove a stored skill (all versions or a specific version).",
        RUNTIME_SKILL_REMOVE,
    ),
)


class SkillRegistrar:
    module_id = "skill"
    is_provider_only = False

    def register(
        self, registry: "ToolRegistry", ctx: "ToolRegisterContext | None" = None
    ) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: "ToolRegisterContext") -> Any:
        del ctx
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=tuple(
                ModelToolDef(
                    model_tool_id=model_tool_id,
                    description=description,
                    parameters={},
                    aliases=(),
                )
                for model_tool_id, description, _runtime_binding_id in _TOOL_DEFINITIONS
            ),
            runtime_bindings=tuple(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_binding_id,
                    model_tool_id=model_tool_id,
                    runtime_candidates=(model_tool_id,),
                )
                for model_tool_id, _description, runtime_binding_id in _TOOL_DEFINITIONS
            ),
        )


REGISTRAR = SkillRegistrar()
