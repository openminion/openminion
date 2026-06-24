from typing import Any

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)

BROWSER_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION_BROWSER = BROWSER_PLUGIN_INTERFACE_VERSION
validate_contract_version_browser = ContractValidator.validate_contract_version
is_compatible_browser = ContractValidator.is_compatible


class BrowserRequestEnvelope(ToolRequestEnvelope):
    pass


class BrowserResultEnvelope(ToolResultEnvelope):
    pass


class BrowserErrorEnvelope(ToolErrorEnvelope):
    pass


class BrowserOperationSchema(BaseModel):
    operation: str
    parameters: dict[str, Any]
    contract_version: str = BROWSER_PLUGIN_INTERFACE_VERSION


__all__ = [
    "BROWSER_PLUGIN_INTERFACE_VERSION",
    "BrowserErrorEnvelope",
    "BrowserOperationSchema",
    "BrowserRequestEnvelope",
    "BrowserResultEnvelope",
    "CONTRACT_VERSION_BROWSER",
    "is_compatible_browser",
    "validate_contract_version_browser",
]
