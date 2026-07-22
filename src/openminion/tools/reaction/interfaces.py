from typing import Any

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


REACTIONS_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = REACTIONS_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class ReactionsRequestEnvelope(ToolRequestEnvelope): ...


class ReactionsResultEnvelope(ToolResultEnvelope): ...


class ReactionsErrorEnvelope(ToolErrorEnvelope): ...


class ReactionsOperationSchema(BaseModel):
    operation: str
    parameters: dict[str, Any]
    contract_version: str = REACTIONS_PLUGIN_INTERFACE_VERSION
