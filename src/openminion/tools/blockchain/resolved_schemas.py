from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from .schema_types import (
    ClosedModel,
    DecimalString,
    PreparationDigest,
    TransactionHash,
    parse_json_container,
)
from .transaction_schemas import OperationState, PostconditionResult


class ResolvedContractCallInspectArgs(ClosedModel):
    action: Literal["resolved_contract_call"]
    resolution_digest: PreparationDigest
    function_signature: str = Field(min_length=1)
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
