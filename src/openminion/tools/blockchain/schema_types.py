from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]
TransactionHash = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{64}$")]
HexData = Annotated[str, Field(pattern=r"^0x(?:[0-9a-fA-F]{2})*$")]
DecimalString = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)$")]
PreparationDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

_SOLIDITY_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_ABI_ARRAY_SUFFIX_RE = re.compile(r"(?:\[(?:0|[1-9][0-9]*)?\])+$")
_ABI_INT_RE = re.compile(r"^(?:u?int)(?:([0-9]+))?$")
_ABI_BYTES_RE = re.compile(r"^bytes(?:([0-9]+))?$")


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def parse_json_container(
    value: Any, expected_type: type[dict[Any, Any]] | type[list[Any]]
) -> Any:
    if value is None:
        return value
    if isinstance(value, expected_type):
        decoded = value
    else:
        if not isinstance(value, str):
            raise ValueError(f"expected a JSON {expected_type.__name__}")
        try:
            decoded = json.loads(
                value,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (json.JSONDecodeError, ValueError):
            return value
        if not isinstance(decoded, expected_type):
            raise ValueError(f"expected a JSON {expected_type.__name__}")
    if not _is_json_value(decoded):
        raise ValueError("container contains a non-JSON value")
    return decoded


def inline_discriminated_branches(schema: dict[str, Any]) -> dict[str, Any]:
    definitions = dict(schema.get("$defs", {}))
    branches: list[dict[str, Any]] = []
    for branch in schema.get("oneOf", []):
        reference = branch.get("$ref", "")
        name = reference.removeprefix("#/$defs/")
        if name and name in definitions:
            resolved = definitions.pop(name)
            resolved.pop("title", None)
            branches.append(resolved)
        else:
            branches.append(branch)
    schema["oneOf"] = branches
    schema["$defs"] = definitions
    schema["discriminator"] = {"propertyName": schema["discriminator"]["propertyName"]}
    return schema


def _valid_abi_type(value: str) -> bool:
    token = value
    suffix = _ABI_ARRAY_SUFFIX_RE.search(token)
    if suffix is not None:
        token = token[: suffix.start()]
    if token in {"address", "bool", "string", "tuple"}:
        return True
    int_match = _ABI_INT_RE.fullmatch(token)
    if int_match is not None:
        width = int_match.group(1)
        return width is None or (8 <= int(width) <= 256 and int(width) % 8 == 0)
    bytes_match = _ABI_BYTES_RE.fullmatch(token)
    if bytes_match is not None:
        width = bytes_match.group(1)
        return width is None or 1 <= int(width) <= 32
    return False


class AbiParameter(ClosedModel):
    name: str
    type: str
    components: list[AbiParameter] | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if not _valid_abi_type(value):
            raise ValueError("unsupported Solidity ABI type")
        return value

    @model_validator(mode="after")
    def validate_components(self) -> AbiParameter:
        base_type = _ABI_ARRAY_SUFFIX_RE.sub("", self.type)
        if base_type == "tuple" and self.components is None:
            raise ValueError("tuple ABI parameters require components")
        if base_type != "tuple" and self.components is not None:
            raise ValueError("non-tuple ABI parameters cannot have components")
        return self


class FunctionAbi(ClosedModel):
    type: Literal["function"]
    name: str
    inputs: list[AbiParameter]
    outputs: list[AbiParameter]
    stateMutability: Literal["view", "pure", "nonpayable", "payable"]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SOLIDITY_IDENTIFIER_RE.fullmatch(value):
            raise ValueError("function name must be a Solidity identifier")
        return value


class ErrorAbi(ClosedModel):
    type: Literal["error"]
    name: str
    inputs: list[AbiParameter]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SOLIDITY_IDENTIFIER_RE.fullmatch(value):
            raise ValueError("error name must be a Solidity identifier")
        return value


class EventInput(AbiParameter):
    indexed: bool


class EventAbi(ClosedModel):
    type: Literal["event"]
    name: str
    inputs: list[EventInput]
    anonymous: Literal[False]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SOLIDITY_IDENTIFIER_RE.fullmatch(value):
            raise ValueError("event name must be a Solidity identifier")
        return value


class DataUnavailableRevert(ClosedModel):
    kind: Literal["data_unavailable"]
    raw_data: None


class UnknownRevert(ClosedModel):
    kind: Literal["unknown"]
    raw_data: HexData


class StandardErrorRevert(ClosedModel):
    kind: Literal["standard_error"]
    reason: str
    raw_data: HexData


class PanicRevert(ClosedModel):
    kind: Literal["panic"]
    code: DecimalString
    raw_data: HexData


class CustomErrorRevert(ClosedModel):
    kind: Literal["custom_error"]
    signature: str
    arguments: list[Any]
    raw_data: HexData


RevertFact = Annotated[
    DataUnavailableRevert
    | UnknownRevert
    | StandardErrorRevert
    | PanicRevert
    | CustomErrorRevert,
    Field(discriminator="kind"),
]


class DecodedEventArgument(ClosedModel):
    name: str
    indexed: bool
    value_kind: Literal["value", "keccak256"]
    value: Any


class DecodedEvent(ClosedModel):
    contract_address: Address
    log_index: DecimalString
    arguments: list[DecodedEventArgument]


class BlockchainError(ClosedModel):
    code: Literal[
        "FEATURE_DISABLED",
        "DEPENDENCY_MISSING",
        "INVALID_ARGUMENT",
        "RPC_UNAVAILABLE",
        "CHAIN_MISMATCH",
        "SIGNER_UNAVAILABLE",
        "SIGNER_MISMATCH",
        "SIMULATION_REVERTED",
        "FEE_CAP_EXCEEDED",
        "STALE_PREPARATION",
        "POLICY_MODE_UNSUPPORTED",
        "CONFIRM_REQUIRED",
        "BROADCAST_UNKNOWN",
        "TRANSACTION_REVERTED",
        "RECEIPT_PENDING",
        "ABI_DECODE_FAILED",
        "RESULT_LIMIT_EXCEEDED",
        "TRANSACTION_NOT_FOUND",
    ]
    message: str
    retryable: Literal[False]
    details: dict[str, Any]
