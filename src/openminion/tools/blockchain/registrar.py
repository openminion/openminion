from __future__ import annotations

from typing import Any

from openminion.modules.tool import ToolRegistry

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_BLOCKCHAIN_INSPECT,
    MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
    MODEL_BLOCKCHAIN_SEND_TRANSACTION,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_BLOCKCHAIN_INSPECT,
    RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
    RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
)

from .plugin import (
    BLOCKCHAIN_INSPECT_DESCRIPTION,
    BLOCKCHAIN_PREPARE_DESCRIPTION,
    BLOCKCHAIN_SEND_DESCRIPTION,
    register,
)


class BlockchainRegistrar:
    module_id = "blockchain"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: Any | None = None) -> None:
        register(registry)

    def get_manifest(self, ctx: Any) -> ToolBindingManifest:
        model_tools = (
            ModelToolDef(
                model_tool_id=MODEL_BLOCKCHAIN_INSPECT,
                description=BLOCKCHAIN_INSPECT_DESCRIPTION,
                parameters={},
            ),
            ModelToolDef(
                model_tool_id=MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
                description=BLOCKCHAIN_PREPARE_DESCRIPTION,
                parameters={},
            ),
            ModelToolDef(
                model_tool_id=MODEL_BLOCKCHAIN_SEND_TRANSACTION,
                description=BLOCKCHAIN_SEND_DESCRIPTION,
                parameters={},
            ),
        )
        runtime_bindings = tuple(
            RuntimeBindingDef(
                runtime_binding_id=runtime_id,
                model_tool_id=model_id,
                runtime_candidates=(model_id,),
            )
            for model_id, runtime_id in zip(
                (
                    MODEL_BLOCKCHAIN_INSPECT,
                    MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
                    MODEL_BLOCKCHAIN_SEND_TRANSACTION,
                ),
                (
                    RUNTIME_BLOCKCHAIN_INSPECT,
                    RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
                    RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
                ),
                strict=True,
            )
        )
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=model_tools,
            runtime_bindings=runtime_bindings,
        )


REGISTRAR = BlockchainRegistrar()
