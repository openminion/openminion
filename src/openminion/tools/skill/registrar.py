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
            module_id="skill",
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_SKILL_INGEST,
                    description="Ingest a skill definition (Markdown) and store it for reuse. Returns skill_id, version_hash, and a rendered snippet for immediate use.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_SKILL_INGEST_URL,
                    description="Fetch a remote markdown skill URL, safety-scan it, and store it for reuse in one step. Use this when the user wants to learn or ingest a skill directly from an http/https URL.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_SKILL_INSPECT,
                    description="Inspect skill markdown for safety and risk issues before ingestion.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_SKILL_LIST,
                    description="List stored skills with optional filters.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_SKILL_GET,
                    description="Get one stored skill by ID and optional version hash.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_SKILL_REMOVE,
                    description="Remove a stored skill (all versions or a specific version).",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_SKILL_INGEST,
                    model_tool_id=MODEL_SKILL_INGEST,
                    runtime_candidates=("skill.ingest",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_SKILL_INGEST_URL,
                    model_tool_id=MODEL_SKILL_INGEST_URL,
                    runtime_candidates=("skill.ingest_url",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_SKILL_INSPECT,
                    model_tool_id=MODEL_SKILL_INSPECT,
                    runtime_candidates=("skill.inspect",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_SKILL_LIST,
                    model_tool_id=MODEL_SKILL_LIST,
                    runtime_candidates=("skill.list",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_SKILL_GET,
                    model_tool_id=MODEL_SKILL_GET,
                    runtime_candidates=("skill.get",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_SKILL_REMOVE,
                    model_tool_id=MODEL_SKILL_REMOVE,
                    runtime_candidates=("skill.remove",),
                ),
            ),
        )


REGISTRAR = SkillRegistrar()
