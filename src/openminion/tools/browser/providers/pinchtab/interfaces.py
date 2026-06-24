from typing import Any

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


PINCHTAB_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = PINCHTAB_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class PinchTabRequestEnvelope(ToolRequestEnvelope):
    """Specialized request envelope for PinchTab plugin methods."""


class PinchTabResultEnvelope(ToolResultEnvelope):
    """Specialized result envelope for PinchTab plugin methods."""


class PinchTabErrorEnvelope(ToolErrorEnvelope):
    """Specialized error envelope for PinchTab plugin methods."""


class PinchTabOperationSchema(BaseModel):
    """Schema definition specifically for PinchTab operations."""

    operation: str
    parameters: dict[str, Any]
    contract_version: str = PINCHTAB_PLUGIN_INTERFACE_VERSION
