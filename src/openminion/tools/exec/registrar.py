from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_EXEC_CLEAR,
    RUNTIME_EXEC_KILL,
    RUNTIME_EXEC_LIST,
    RUNTIME_EXEC_PASTE,
    RUNTIME_EXEC_POLL,
    RUNTIME_EXEC_RUN,
    RUNTIME_EXEC_SEND_KEYS,
    RUNTIME_EXEC_SUBMIT,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class ExecRegistrar:
    """Registrar with manifest for exec tool module."""

    module_id = "exec"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register exec tools with runtime registry."""
        from .plugin import register as tool_register

        tool_register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return ToolBindingManifest for exec module."""
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id="exec",
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_RUN,
                    description=(
                        "Run one allowlisted direct shell command for verification "
                        "or existing-file workflows; do not use pipes, "
                        "redirections, chaining, or fallback operators. For "
                        "toolchain checks, use direct discovery such as "
                        "`command -v nasm`, then a separate direct version check "
                        "such as `nasm --version`. Prefer host.metrics for disk, "
                        "memory, and OS status; prefer structured file/web tools "
                        "for reads, scaffolding, or web fetches."
                    ),
                    parameters={},
                    aliases=("exec_run",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_POLL,
                    description="Poll process status",
                    parameters={},
                    aliases=("exec_poll",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_SEND_KEYS,
                    description="Send keys to process",
                    parameters={},
                    aliases=("exec_send_keys",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_SUBMIT,
                    description="Submit new process",
                    parameters={},
                    aliases=("exec_submit",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_PASTE,
                    description="Paste to process",
                    parameters={},
                    aliases=("exec_paste",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_KILL,
                    description="Kill process",
                    parameters={},
                    aliases=("exec_kill",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_CLEAR,
                    description="Clear process buffer",
                    parameters={},
                    aliases=("exec_clear",),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_EXEC_LIST,
                    description="List processes",
                    parameters={},
                    aliases=("exec_list",),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_RUN,
                    model_tool_id=MODEL_EXEC_RUN,
                    runtime_candidates=("exec.run",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_POLL,
                    model_tool_id=MODEL_EXEC_POLL,
                    runtime_candidates=("exec.poll",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_SEND_KEYS,
                    model_tool_id=MODEL_EXEC_SEND_KEYS,
                    runtime_candidates=("exec.send_keys",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_SUBMIT,
                    model_tool_id=MODEL_EXEC_SUBMIT,
                    runtime_candidates=("exec.submit",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_PASTE,
                    model_tool_id=MODEL_EXEC_PASTE,
                    runtime_candidates=("exec.paste",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_KILL,
                    model_tool_id=MODEL_EXEC_KILL,
                    runtime_candidates=("exec.kill",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_CLEAR,
                    model_tool_id=MODEL_EXEC_CLEAR,
                    runtime_candidates=("exec.clear",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_EXEC_LIST,
                    model_tool_id=MODEL_EXEC_LIST,
                    runtime_candidates=("exec.list",),
                ),
            ),
        )


REGISTRAR = ExecRegistrar()
