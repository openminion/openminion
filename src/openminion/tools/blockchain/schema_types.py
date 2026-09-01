from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


def _valid_abi_type(value: str) -> bool:
    token = value
    suffix = _ABI_ARRAY_SUFFIX_RE.search(token)
    if suffix is not None:
        token = token[: suffix.start()]
    if token in {"address", "bool", "string"}:
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

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if not _valid_abi_type(value):
            raise ValueError("unsupported non-tuple Solidity ABI type")
        return value


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
    ]
    message: str
    retryable: Literal[False]
    details: dict[str, Any]
