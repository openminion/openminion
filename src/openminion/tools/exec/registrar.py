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
        del ctx
        from openminion.modules.tool.contracts import ToolBindingManifest

        return ToolBindingManifest(
            module_id="exec",
            model_tools=_exec_model_tools(),
            runtime_bindings=_exec_runtime_bindings(),
        )


def _exec_model_tools() -> tuple[Any, ...]:
    from openminion.modules.tool.contracts import ModelToolDef

    return (
        ModelToolDef(MODEL_EXEC_RUN, _EXEC_RUN_DESCRIPTION, {}, ("exec_run",)),
        ModelToolDef(MODEL_EXEC_POLL, "Poll process status", {}, ("exec_poll",)),
        ModelToolDef(MODEL_EXEC_SEND_KEYS, "Send keys to process", {}, ("exec_send_keys",)),
        ModelToolDef(MODEL_EXEC_SUBMIT, "Submit new process", {}, ("exec_submit",)),
        ModelToolDef(MODEL_EXEC_PASTE, "Paste to process", {}, ("exec_paste",)),
        ModelToolDef(MODEL_EXEC_KILL, "Kill process", {}, ("exec_kill",)),
        ModelToolDef(MODEL_EXEC_CLEAR, "Clear process buffer", {}, ("exec_clear",)),
        ModelToolDef(MODEL_EXEC_LIST, "List processes", {}, ("exec_list",)),
    )


def _exec_runtime_bindings() -> tuple[Any, ...]:
    from openminion.modules.tool.contracts import RuntimeBindingDef

    return tuple(
        RuntimeBindingDef(runtime_id, model_id, (candidate,))
        for runtime_id, model_id, candidate in (
            (RUNTIME_EXEC_RUN, MODEL_EXEC_RUN, "exec.run"),
            (RUNTIME_EXEC_POLL, MODEL_EXEC_POLL, "exec.poll"),
            (RUNTIME_EXEC_SEND_KEYS, MODEL_EXEC_SEND_KEYS, "exec.send_keys"),
            (RUNTIME_EXEC_SUBMIT, MODEL_EXEC_SUBMIT, "exec.submit"),
            (RUNTIME_EXEC_PASTE, MODEL_EXEC_PASTE, "exec.paste"),
            (RUNTIME_EXEC_KILL, MODEL_EXEC_KILL, "exec.kill"),
            (RUNTIME_EXEC_CLEAR, MODEL_EXEC_CLEAR, "exec.clear"),
            (RUNTIME_EXEC_LIST, MODEL_EXEC_LIST, "exec.list"),
        )
    )


_EXEC_RUN_DESCRIPTION = (
    "Run one allowlisted direct shell command for verification or existing-file "
    "workflows; do not use pipes, redirections, chaining, or fallback operators. "
    "For toolchain checks, use direct discovery such as `command -v nasm`, then "
    "a separate direct version check such as `nasm --version`. Prefer "
    "host.metrics for disk, memory, and OS status; prefer structured file/web "
    "tools for reads, scaffolding, or web fetches."
)



REGISTRAR = ExecRegistrar()
