from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, RootModel, TypeAdapter

from .schema_types import (
    Address,
    ClosedModel,
    DecimalString,
    FunctionAbi,
    HexData,
    TransactionHash,
)


class ChainSummaryArgs(ClosedModel):
    action: Literal["chain_summary"]


class NativeBalanceArgs(ClosedModel):
    action: Literal["native_balance"]
    address: Address


class BytecodeArgs(ClosedModel):
    action: Literal["bytecode"]
    address: Address


class ContractReadArgs(ClosedModel):
    action: Literal["contract_read"]
    contract_address: Address
    function_abi: FunctionAbi
    function_args: list[Any]


class TransactionArgs(ClosedModel):
    action: Literal["transaction"]
    transaction_hash: TransactionHash


class ReceiptArgs(ClosedModel):
    action: Literal["receipt"]
    transaction_hash: TransactionHash


InspectRequest = Annotated[
    ChainSummaryArgs
    | NativeBalanceArgs
    | BytecodeArgs
    | ContractReadArgs
    | TransactionArgs
    | ReceiptArgs,
    Field(discriminator="action"),
]
INSPECT_REQUEST_ADAPTER: TypeAdapter[InspectRequest] = TypeAdapter(InspectRequest)


class InspectArgs(RootModel[InspectRequest]):
    pass


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


InspectResult = Annotated[
    ChainSummaryResult
    | NativeBalanceResult
    | BytecodeResult
    | ContractReadResult
    | TransactionResult
    | ReceiptResult,
    Field(discriminator="action"),
]
INSPECT_RESULT_ADAPTER: TypeAdapter[InspectResult] = TypeAdapter(InspectResult)
