from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel, TypeAdapter, field_validator

from .schema_types import (
    Address,
    ClosedModel,
    DecimalString,
    FunctionAbi,
    HexData,
    PreparationDigest,
    TransactionHash,
    inline_discriminated_branches,
    parse_json_container,
)
from .transaction_schemas import OperationState, PostconditionResult


class ChainSummaryArgs(ClosedModel):
    action: Literal["chain_summary"]


class NativeBalanceArgs(ClosedModel):
    action: Literal["native_balance"]
    address: Address


class BytecodeArgs(ClosedModel):
    action: Literal["bytecode"]
    address: Address


class ContractReadArgs(ClosedModel):
    action: Literal["contract_read"] = Field(
        description=(
            "Required discriminator; use exactly contract_read to read one contract "
            "function result, including quotes and state. Use blockchain.debug only "
            "for call or revert diagnosis."
        )
    )
    contract_address: Address
    function_abi: FunctionAbi = Field(
        description="JSON object; a strict JSON object string is also accepted."
    )
    function_args: list[Any] = Field(
        description=(
            "JSON array in ABI input order; a strict JSON array string is also "
            "accepted."
        )
    )

    @field_validator("function_abi", mode="before")
    @classmethod
    def parse_function_abi(cls, value: Any) -> Any:
        return parse_json_container(value, dict)

    @field_validator("function_args", mode="before")
    @classmethod
    def parse_function_args(cls, value: Any) -> Any:
        return parse_json_container(value, list)


class TransactionArgs(ClosedModel):
    action: Literal["transaction"]
    transaction_hash: TransactionHash


class ReceiptArgs(ClosedModel):
    action: Literal["receipt"] = Field(
        description=(
            "Use exactly receipt with blockchain.inspect to verify transaction receipt "
            "status, even if a prior send returned receipt status. When receipt status "
            "and event decoding are both requested in order, perform this read before "
            "blockchain.debug transaction_events."
        )
    )
    transaction_hash: TransactionHash


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


InspectRequest = Annotated[
    ChainSummaryArgs
    | NativeBalanceArgs
    | BytecodeArgs
    | ContractReadArgs
    | TransactionArgs
    | ReceiptArgs
    | ResolvedContractCallInspectArgs
    | OperationStatusArgs,
    Field(discriminator="action"),
]
INSPECT_REQUEST_ADAPTER: TypeAdapter[InspectRequest] = TypeAdapter(InspectRequest)


class InspectArgs(RootModel[InspectRequest]):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "action": "contract_read",
                    "contract_address": "0x" + "22" * 20,
                    "function_abi": {
                        "type": "function",
                        "name": "quote",
                        "inputs": [{"name": "amountIn", "type": "uint256"}],
                        "outputs": [{"name": "amountOut", "type": "uint256"}],
                        "stateMutability": "view",
                    },
                    "function_args": [7],
                },
                {
                    "action": "receipt",
                    "transaction_hash": "0x" + "aa" * 32,
                },
            ]
        }
    )

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return inline_discriminated_branches(super().model_json_schema(*args, **kwargs))


class ChainSummaryData(ClosedModel):
    chain_id: int = Field(ge=1)
    latest_block_number: DecimalString


class NativeBalanceData(ClosedModel):
    chain_id: int = Field(ge=1)
    address: Address
    balance_wei: DecimalString


class BytecodeData(ClosedModel):
    chain_id: int = Field(ge=1)
    address: Address
    has_code: bool
    bytecode: HexData


class ContractReadData(ClosedModel):
    chain_id: int = Field(ge=1)
    contract_address: Address
    function_signature: str = Field(min_length=1)
    return_values: list[Any]


class TransactionData(ClosedModel):
    chain_id: int = Field(ge=1)
    transaction_hash: TransactionHash
    from_address: Address
    to_address: Address | None
    value_wei: DecimalString
    input: HexData
    nonce: DecimalString
    block_number: DecimalString | None


class ReceiptData(ClosedModel):
    chain_id: int = Field(ge=1)
    transaction_hash: TransactionHash
    state: Literal["pending", "succeeded", "reverted"]
    block_number: DecimalString | None
    status: Literal[0, 1] | None
    gas_used: DecimalString | None
    effective_gas_price_wei: DecimalString | None


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


class ChainSummaryResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["chain_summary"]
    data: ChainSummaryData


class NativeBalanceResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["native_balance"]
    data: NativeBalanceData


class BytecodeResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["bytecode"]
    data: BytecodeData


class ContractReadResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["contract_read"]
    data: ContractReadData


class TransactionResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["transaction"]
    data: TransactionData


class ReceiptResult(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["receipt"]
    data: ReceiptData


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


InspectResult = Annotated[
    ChainSummaryResult
    | NativeBalanceResult
    | BytecodeResult
    | ContractReadResult
    | TransactionResult
    | ReceiptResult
    | ResolvedContractCallResult
    | OperationStatusResult,
    Field(discriminator="action"),
]
INSPECT_RESULT_ADAPTER: TypeAdapter[InspectResult] = TypeAdapter(InspectResult)
