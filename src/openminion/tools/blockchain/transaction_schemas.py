from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, RootModel, TypeAdapter, field_validator

from .schema_types import (
    Address,
    ClosedModel,
    DecimalString,
    FunctionAbi,
    HexData,
    PreparationDigest,
)


class NativeTransferArgs(ClosedModel):
    kind: Literal["native_transfer"]
    to_address: Address
    value_wei: DecimalString


class RawCallArgs(ClosedModel):
    kind: Literal["raw_call"]
    to_address: Address
    data: HexData
    value_wei: DecimalString = "0"


class ContractCallArgs(ClosedModel):
    kind: Literal["contract_call"]
    contract_address: Address
    function_abi: FunctionAbi
    function_args: list[Any]
    value_wei: DecimalString = "0"


PrepareRequest = Annotated[
    NativeTransferArgs | RawCallArgs | ContractCallArgs,
    Field(discriminator="kind"),
]
PREPARE_REQUEST_ADAPTER: TypeAdapter[PrepareRequest] = TypeAdapter(PrepareRequest)


class PrepareArgs(RootModel[PrepareRequest]):
    pass


class Eip1559Transaction(ClosedModel):
    schema_version: Literal["evm-transaction-v1"]
    transaction_type: Literal["eip1559"]
    chain_id: int = Field(ge=1)
    from_address: Address
    to_address: Address
    value_wei: DecimalString
    nonce: DecimalString
    gas_limit: DecimalString
    data: HexData
    max_fee_per_gas_wei: DecimalString
    max_priority_fee_per_gas_wei: DecimalString
    max_total_fee_wei: DecimalString


class LegacyTransaction(ClosedModel):
    schema_version: Literal["evm-transaction-v1"]
    transaction_type: Literal["legacy"]
    chain_id: int = Field(ge=1)
    from_address: Address
    to_address: Address
    value_wei: DecimalString
    nonce: DecimalString
    gas_limit: DecimalString
    data: HexData
    gas_price_wei: DecimalString
    max_total_fee_wei: DecimalString


NormalizedTransaction = Annotated[
    Eip1559Transaction | LegacyTransaction,
    Field(discriminator="transaction_type"),
]


class CallContext(ClosedModel):
    function_abi: FunctionAbi
    function_args: list[Any]
    function_signature: str = Field(min_length=1)


class SendTransactionArgs(ClosedModel):
    transaction: NormalizedTransaction = Field(
        description="Copy the transaction object from blockchain.prepare_transaction."
    )
    call_context: CallContext | None = Field(
        description=(
            "Copy call_context from blockchain.prepare_transaction; use null for "
            "native transfers and raw calls."
        )
    )
    preparation_digest: PreparationDigest

    @field_validator("transaction", "call_context", mode="before")
    @classmethod
    def decode_json_object(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        return decoded if isinstance(decoded, dict) else value


SEND_REQUEST_ADAPTER = TypeAdapter(SendTransactionArgs)


class SimulationResult(ClosedModel):
    state: Literal["succeeded"]


class PreparedTransactionResult(ClosedModel):
    ok: Literal[True]
    state: Literal["prepared"]
    transaction: NormalizedTransaction
    call_context: CallContext | None
    simulation: SimulationResult
    preparation_digest: PreparationDigest
