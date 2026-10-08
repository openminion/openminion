from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator
from urllib.parse import urlsplit

from .schema_types import (
    Address,
    ClosedModel,
    DecimalString,
    FunctionAbi,
    PreparationDigest,
    TransactionHash,
    parse_json_container,
)
from .transaction_schemas import OperationState, PostconditionResult


def _validate_https_url(value: str, *, query_allowed: bool) -> str:
    normalized = value.strip()
    if len(normalized) > 2048:
        raise ValueError("URL must be at most 2048 characters")
    parsed = urlsplit(normalized)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (not query_allowed and (parsed.query or parsed.fragment))
    ):
        raise ValueError("URL must be a valid HTTPS URL")
    return normalized


class ExpectedCheckpoint(ClosedModel):
    block_number: DecimalString
    block_hash: TransactionHash


class ResolveContractArgs(ClosedModel):
    rpc_url: str
    expected_chain_id: int = Field(ge=1)
    expected_genesis_hash: TransactionHash
    expected_checkpoint: ExpectedCheckpoint | None = None
    contract_address: Address
    explorer_contract_url: str | None = None
    research_source_urls: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("rpc_url")
    @classmethod
    def validate_rpc_url(cls, value: str) -> str:
        return _validate_https_url(value, query_allowed=False)

    @field_validator("explorer_contract_url")
    @classmethod
    def validate_explorer_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_https_url(value, query_allowed=True)

    @field_validator("research_source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        return [_validate_https_url(value, query_allowed=True) for value in values]


class ResolvedContractRecord(ClosedModel):
    resolution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rpc_url: str
    sourcify_target_url: str
    sourcify_target_match: Literal["match", "exact_match"]
    sourcify_implementation_url: str | None
    sourcify_implementation_match: Literal["match", "exact_match"] | None
    expected_chain_id: int = Field(ge=1)
    observed_chain_id: int = Field(ge=1)
    expected_genesis_hash: TransactionHash
    observed_genesis_hash: TransactionHash
    expected_checkpoint: ExpectedCheckpoint | None
    observed_checkpoint: ExpectedCheckpoint | None
    verification_block_number: DecimalString
    verification_block_hash: TransactionHash
    contract_address: Address
    proxy_kind: Literal["direct", "eip1967"]
    implementation_address: Address | None
    abi_address: Address
    target_code_hash: TransactionHash
    implementation_code_hash: TransactionHash | None
    function_abi: list[FunctionAbi]
    explorer_contract_url: str | None
    research_source_urls: list[str]
    verification_statement: str


class ResolvedContractData(ClosedModel):
    resolution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rpc_origin: str
    sourcify_origin: Literal["https://sourcify.dev"]
    sourcify_target_match: Literal["match", "exact_match"]
    sourcify_implementation_match: Literal["match", "exact_match"] | None
    expected_chain_id: int = Field(ge=1)
    observed_chain_id: int = Field(ge=1)
    expected_genesis_hash: TransactionHash
    observed_genesis_hash: TransactionHash
    expected_checkpoint: ExpectedCheckpoint | None
    observed_checkpoint: ExpectedCheckpoint | None
    verification_block_number: DecimalString
    verification_block_hash: TransactionHash
    contract_address: Address
    proxy_kind: Literal["direct", "eip1967"]
    implementation_address: Address | None
    abi_address: Address
    target_code_hash: TransactionHash
    implementation_code_hash: TransactionHash | None
    function_abi: list[FunctionAbi]
    available_function_signatures: list[str]
    explorer_contract_url: str | None
    research_source_urls: list[str]
    verification_statement: str


class ResolveContractSuccess(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["resolve_contract"]
    data: ResolvedContractData


ResolutionErrorCode = Literal[
    "INVALID_ARGUMENT",
    "ENDPOINT_FORBIDDEN",
    "RPC_UNAVAILABLE",
    "CHAIN_IDENTITY_MISMATCH",
    "STALE_BLOCK",
    "STALE_RESOLUTION",
    "RESOLUTION_UNAVAILABLE",
    "RESOLUTION_INVALID",
    "FUNCTION_UNAVAILABLE",
    "SIMULATION_REVERTED",
    "EMPTY_CONTRACT_CODE",
    "UNSUPPORTED_PROXY",
    "METADATA_UNAVAILABLE",
    "CONTRACT_NOT_VERIFIED",
    "ABI_INVALID",
    "RESULT_TOO_LARGE",
]


class ResolutionError(ClosedModel):
    code: ResolutionErrorCode
    message: str
    retryable: Literal[False]
    details: dict[str, Any]


class ResolveContractError(ClosedModel):
    ok: Literal[False]
    state: Literal["failed"]
    error: ResolutionError


ResolveContractResult = Annotated[
    ResolveContractSuccess | ResolveContractError,
    Field(discriminator="ok"),
]
RESOLVE_CONTRACT_RESULT_ADAPTER: TypeAdapter[ResolveContractResult] = TypeAdapter(
    ResolveContractResult
)


class ResolvedContractCallInspectArgs(ClosedModel):
    action: Literal["resolved_contract_call"]
    resolution_digest: PreparationDigest
    function_signature: str = Field(
        min_length=1,
        description=(
            "Copy one exact canonical signature from the resolver's "
            "available_function_signatures list; do not include names or return types."
        ),
    )
    arguments: list[Any]

    @field_validator("arguments", mode="before")
    @classmethod
    def parse_arguments(cls, value: Any) -> Any:
        return parse_json_container(value, list)


class OperationStatusArgs(ClosedModel):
    action: Literal["operation_status"]
    preparation_digest: PreparationDigest


class ResolvedContractCallData(ClosedModel):
    resolution_digest: PreparationDigest
    block_number: DecimalString
    block_hash: TransactionHash
    function_signature: str
    arguments: list[Any]
    raw_return_digest: PreparationDigest
    result: list[Any]


class OperationStatusData(ClosedModel):
    preparation_digest: PreparationDigest
    resolution_digest: PreparationDigest
    operation_digest: PreparationDigest
    transaction_hash: TransactionHash
    submission_started: Literal[True]
    broadcast_attempts: Literal[1]
    state: OperationState
    receipt_block_number: DecimalString | None
    receipt_block_hash: TransactionHash | None
    receipt_status: Literal[0, 1] | None
    confirmations: int = Field(ge=0)
    confirmation_depth: int = Field(ge=1, le=64)
    gas_used: DecimalString | None
    effective_gas_price_wei: DecimalString | None
    postcondition_results: list[PostconditionResult] = Field(max_length=5)
    last_error_code: str | None


class ResolvedContractCallResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["resolved_contract_call"]
    data: ResolvedContractCallData


class OperationStatusResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["operation_status"]
    data: OperationStatusData
