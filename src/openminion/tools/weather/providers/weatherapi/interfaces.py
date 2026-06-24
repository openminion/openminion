from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
)


WEATHER_WEATHERAPI_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION

CONTRACT_VERSION = WEATHER_WEATHERAPI_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible
