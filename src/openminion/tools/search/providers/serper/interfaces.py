from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
)


SEARCH_SERPER_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION

CONTRACT_VERSION = SEARCH_SERPER_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible
