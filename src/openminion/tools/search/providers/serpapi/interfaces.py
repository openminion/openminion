from typing import Any

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


SERPAPI_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = SERPAPI_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class SerpApiRequestEnvelope(ToolRequestEnvelope): ...


class SerpApiResultEnvelope(ToolResultEnvelope): ...


class SerpApiErrorEnvelope(ToolErrorEnvelope): ...


class SerpApiOperationSchema(BaseModel):
    operation: str
    parameters: dict[str, Any]
    contract_version: str = SERPAPI_PLUGIN_INTERFACE_VERSION
