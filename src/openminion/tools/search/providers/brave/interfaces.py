from typing import Any, Dict

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


SEARCH_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = SEARCH_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class SearchRequestEnvelope(ToolRequestEnvelope): ...


class SearchResultEnvelope(ToolResultEnvelope): ...


class SearchErrorEnvelope(ToolErrorEnvelope): ...


class SearchOperationSchema(BaseModel):
    operation: str
    parameters: Dict[str, Any]
    contract_version: str = SEARCH_PLUGIN_INTERFACE_VERSION
