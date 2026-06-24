from typing import Any, Dict

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
    """Specialized request envelope for exec plugin methods."""


class ExecResultEnvelope(ToolResultEnvelope):
    """Specialized result envelope for exec plugin methods."""


class ExecErrorEnvelope(ToolErrorEnvelope):
    """Specialized error envelope for exec plugin methods."""


class ExecOperationSchema(BaseModel):
    """Schema definition specifically for exec operations."""

    operation: str
    parameters: Dict[str, Any]
    contract_version: str = EXEC_PLUGIN_INTERFACE_VERSION
