from typing import Any

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


EXEC_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = EXEC_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class ExecRequestEnvelope(ToolRequestEnvelope):
    pass


class ExecResultEnvelope(ToolResultEnvelope):
    pass


class ExecErrorEnvelope(ToolErrorEnvelope):
    pass


class ExecOperationSchema(BaseModel):
    operation: str
    parameters: dict[str, Any]
    contract_version: str = EXEC_PLUGIN_INTERFACE_VERSION
