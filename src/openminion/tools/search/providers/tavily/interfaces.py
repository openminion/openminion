from typing import Any, Dict

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


TAVILY_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = TAVILY_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class TavilyRequestEnvelope(ToolRequestEnvelope): ...


class TavilyResultEnvelope(ToolResultEnvelope): ...


class TavilyErrorEnvelope(ToolErrorEnvelope): ...


class TavilyOperationSchema(BaseModel):
    operation: str
    parameters: Dict[str, Any]
    contract_version: str = TAVILY_PLUGIN_INTERFACE_VERSION
