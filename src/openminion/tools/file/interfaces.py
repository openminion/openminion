from typing import Any, Dict

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


FILE_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = FILE_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class FileRequestEnvelope(ToolRequestEnvelope):
    """Specialized request envelope for file plugin methods."""


class FileResultEnvelope(ToolResultEnvelope):
    """Specialized result envelope for file plugin methods."""


class FileErrorEnvelope(ToolErrorEnvelope):
    """Specialized error envelope for file plugin methods."""


class FileOperationSchema(BaseModel):
    """Schema definition specifically for file operations."""

    operation: str
    parameters: Dict[str, Any]
    contract_version: str = FILE_PLUGIN_INTERFACE_VERSION
