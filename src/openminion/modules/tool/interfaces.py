import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

PLUGIN_CONTRACT_VERSION = "v1"
CONTRACT_VERSION_PATTERN_STR = r"^v\d+(\.\d+)*$"
_CONTRACT_VERSION_RE: re.Pattern[str] = re.compile(CONTRACT_VERSION_PATTERN_STR)


class ContractProtocol(Protocol):
    """Protocol defining objects that carry interface contract information."""

    @property
    def contract_version(self) -> str: ...


class ContractValidator:
    """Validator for tool plugin compatibility based on contract version."""

    @staticmethod
    def validate_contract_version(version: str) -> bool:
        """Validate that a contract version follows the required format."""
        return _CONTRACT_VERSION_RE.match(version) is not None

    @staticmethod
    def is_compatible(contract_a: str, contract_b: str) -> bool:
        return ContractValidator.validate_contract_version(
            contract_a
        ) and ContractValidator.validate_contract_version(contract_b)


class PluginContractError(ValueError):
    """Raised when a plugin object does not satisfy the tool plugin contract."""


@dataclass(frozen=True)
class PluginContract:
    """Normalized plugin contract metadata."""

    tool_id: str
    contract_version: str
    capabilities: tuple[str, ...]


def _validate_version_format(version: str) -> str:
    if not _CONTRACT_VERSION_RE.match(version):
        raise ValueError(
            f"Invalid contract version: {version}"
        )  # allow-bare-raise: pydantic @field_validator body
    return version


def validate_plugin_contract(plugin: Any) -> PluginContract:
    """Validate a class-based plugin object against the baseline contract."""

    tool_id = str(getattr(plugin, "tool_id", "") or "").strip()
    if not tool_id:
        raise PluginContractError("plugin.tool_id is required")

    contract_version = str(getattr(plugin, "contract_version", "") or "").strip()
    if not contract_version:
        raise PluginContractError(f"plugin '{tool_id}' is missing contract_version")
    if not ContractValidator.validate_contract_version(contract_version):
        raise PluginContractError(
            f"plugin '{tool_id}' has invalid contract_version '{contract_version}'"
        )
    if not ContractValidator.is_compatible(contract_version, PLUGIN_CONTRACT_VERSION):
        raise PluginContractError(
            f"plugin '{tool_id}' contract_version '{contract_version}' is incompatible with tool interface '{PLUGIN_CONTRACT_VERSION}'"
        )

    raw_capabilities = getattr(plugin, "capabilities", ())
    if isinstance(raw_capabilities, str):
        capabilities = (raw_capabilities.strip(),) if raw_capabilities.strip() else ()
    elif isinstance(raw_capabilities, (tuple, list, set, frozenset)):
        capabilities = tuple(
            str(item).strip() for item in raw_capabilities if str(item).strip()
        )
    else:
        capabilities = ()
    if not capabilities:
        raise PluginContractError(
            f"plugin '{tool_id}' must declare non-empty capabilities"
        )

    register_fn = getattr(plugin, "register", None)
    if not callable(register_fn):
        raise PluginContractError(
            f"plugin '{tool_id}' must expose callable register(registry)"
        )
    healthcheck_fn = getattr(plugin, "healthcheck", None)
    if not callable(healthcheck_fn):
        raise PluginContractError(
            f"plugin '{tool_id}' must expose callable healthcheck()"
        )

    return PluginContract(
        tool_id=tool_id,
        contract_version=contract_version,
        capabilities=capabilities,
    )


class ToolRequestEnvelope(BaseModel):
    """Envelope for standardizing tool request formatting across modules."""

    method: str
    args: dict[str, Any]
    contract_version: str = PLUGIN_CONTRACT_VERSION

    @field_validator("contract_version")
    @classmethod
    def validate_version_format(cls, v: str) -> str:
        return _validate_version_format(v)


class ToolResultEnvelope(BaseModel):
    """Envelope for standardizing tool response formatting across modules."""

    status: str
    data: dict[str, Any]
    artifacts: dict[str, Any] = Field(default_factory=dict)
    contract_version: str = PLUGIN_CONTRACT_VERSION

    @field_validator("contract_version")
    @classmethod
    def validate_version_format(cls, v: str) -> str:
        return _validate_version_format(v)


class ToolErrorEnvelope(BaseModel):
    """Envelope for standardizing tool error responses across modules."""

    error_code: str
    error_message: str
    details: dict[str, Any] = Field(default_factory=dict)
    contract_version: str = PLUGIN_CONTRACT_VERSION

    @field_validator("contract_version")
    @classmethod
    def validate_version_format(cls, v: str) -> str:
        return _validate_version_format(v)


CONTRACT_VERSION_PATTERN = CONTRACT_VERSION_PATTERN_STR
