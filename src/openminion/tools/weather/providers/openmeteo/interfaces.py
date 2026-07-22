from typing import Any

from pydantic import BaseModel

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


WEATHER_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION
CONTRACT_VERSION = WEATHER_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class WeatherRequestEnvelope(ToolRequestEnvelope): ...


class WeatherResultEnvelope(ToolResultEnvelope): ...


class WeatherErrorEnvelope(ToolErrorEnvelope): ...


class WeatherOperationSchema(BaseModel):
    operation: str
    parameters: dict[str, Any]
    contract_version: str = WEATHER_PLUGIN_INTERFACE_VERSION
