from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, TypeAdapter, model_validator

from .schema_types import (
    ClosedModel,
    DecodedEvent,
    DecimalString,
    HexData,
    RevertFact,
    TransactionHash,
)

Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FeatureDisabledDetails(ClosedModel):
    feature: Literal["blockchain"]


class InvalidArgumentDetails(ClosedModel):
    field: Literal[""]
    reason: Literal["request_schema", "debug_request_size"]


class RpcUnavailableDetails(ClosedModel):
    operation: Literal["simulate", "receipt"]


class ChainMismatchDetails(ClosedModel):
    expected_chain_id: int = Field(ge=1)
    observed_chain_id: int = Field(ge=1)


class SimulationRevertedDetails(ClosedModel):
    stage: Literal["debug"]
    revert: RevertFact
    broadcast_attempted: Literal[False]


class AbiDecodeFailedDetails(ClosedModel):
    operation: Literal["decode_calldata", "decode_revert"]
    data_sha256: Sha256Digest


class EventDecodeFailedDetails(ClosedModel):
    operation: Literal["transaction_events"]
    data_sha256: Sha256Digest
    log_index: str


class ResultLimitExceededDetails(ClosedModel):
    surface: Literal["transaction_events", "debug_result"]
    limit: int = Field(ge=1)
    observed_at_least: int = Field(ge=1)


class ReceiptPendingDetails(ClosedModel):
    transaction_hash: TransactionHash
    accepted: Literal[True]


class TransactionNotFoundDetails(ClosedModel):
    operation: Literal["transaction_events"]
    transaction_hash: TransactionHash


DebugErrorDetails = (
    FeatureDisabledDetails
    | InvalidArgumentDetails
    | RpcUnavailableDetails
    | ChainMismatchDetails
    | SimulationRevertedDetails
    | AbiDecodeFailedDetails
    | EventDecodeFailedDetails
    | ResultLimitExceededDetails
    | ReceiptPendingDetails
    | TransactionNotFoundDetails
)


class DebugError(ClosedModel):
    code: Literal[
        "FEATURE_DISABLED",
        "INVALID_ARGUMENT",
        "RPC_UNAVAILABLE",
        "CHAIN_MISMATCH",
        "SIMULATION_REVERTED",
        "ABI_DECODE_FAILED",
        "RESULT_LIMIT_EXCEEDED",
        "RECEIPT_PENDING",
        "TRANSACTION_NOT_FOUND",
    ]
    message: str
    retryable: Literal[False]
    details: DebugErrorDetails

    _contracts: ClassVar[
        dict[
            str,
            tuple[
                str,
                type[ClosedModel] | tuple[type[ClosedModel], ...],
            ],
        ]
    ] = {
        "FEATURE_DISABLED": (
            "Blockchain capability is disabled.",
            FeatureDisabledDetails,
        ),
        "INVALID_ARGUMENT": (
            "Blockchain arguments are invalid.",
            InvalidArgumentDetails,
        ),
        "RPC_UNAVAILABLE": (
            "Blockchain RPC operation failed.",
            RpcUnavailableDetails,
        ),
        "CHAIN_MISMATCH": (
            "Configured and observed chain IDs differ.",
            ChainMismatchDetails,
        ),
        "SIMULATION_REVERTED": (
            "Transaction simulation reverted.",
            SimulationRevertedDetails,
        ),
        "ABI_DECODE_FAILED": (
            "Blockchain ABI data could not be decoded.",
            (AbiDecodeFailedDetails, EventDecodeFailedDetails),
        ),
        "RESULT_LIMIT_EXCEEDED": (
            "Blockchain diagnostic result exceeds the supported limit.",
            ResultLimitExceededDetails,
        ),
        "RECEIPT_PENDING": (
            "Transaction was accepted but is not yet mined.",
            ReceiptPendingDetails,
        ),
        "TRANSACTION_NOT_FOUND": (
            "Blockchain transaction was not found.",
            TransactionNotFoundDetails,
        ),
    }

    @model_validator(mode="after")
    def validate_code_contract(self) -> DebugError:
        message, details_type = self._contracts[self.code]
        if self.message != message or not isinstance(self.details, details_type):
            raise ValueError("debug error does not match its code contract")
        return self


class DebugFailure(ClosedModel):
    ok: Literal[False]
    state: Literal["failed"]
    error: DebugError


class DebugSimulationData(ClosedModel):
    chain_id: DecimalString
    block_identifier: str
    resolved_block_number: DecimalString | None
    resolved_block_hash: HexData | None
    return_data: HexData
    gas_estimate: DecimalString
    decoded_returns: list[Any] | None


class SimulateCallResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["simulate_call"]
    data: DebugSimulationData


class DecodeCalldataResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["decode_calldata"]
    function_signature: str
    arguments: list[Any]


class DecodeRevertResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["decode_revert"]
    revert: RevertFact


class TransactionEventsResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["transaction_events"]
    chain_id: DecimalString
    transaction_hash: TransactionHash
    receipt_block_number: DecimalString
    event_signature: str
    events: list[DecodedEvent]


DebugResult = (
    SimulateCallResult
    | DecodeCalldataResult
    | DecodeRevertResult
    | TransactionEventsResult
    | DebugFailure
)
DEBUG_RESULT_ADAPTER: TypeAdapter[DebugResult] = TypeAdapter(DebugResult)
