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
    inline_discriminated_branches,
    parse_json_container,
)


class NativeTransferArgs(ClosedModel):
    kind: Literal["native_transfer"] = Field(
        description="Required discriminator; use exactly native_transfer."
    )
    to_address: Address
    value_wei: DecimalString


class RawCallArgs(ClosedModel):
    kind: Literal["raw_call"] = Field(
        description="Required discriminator; use exactly raw_call."
    )
    to_address: Address
    data: HexData
    value_wei: DecimalString = "0"


class ContractCallArgs(ClosedModel):
    kind: Literal["contract_call"] = Field(
        description="Required discriminator; use exactly contract_call."
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
    value_wei: DecimalString = "0"

    @field_validator("function_abi", mode="before")
    @classmethod
    def parse_function_abi(cls, value: Any) -> Any:
        return parse_json_container(value, dict)

    @field_validator("function_args", mode="before")
    @classmethod
    def parse_function_args(cls, value: Any) -> Any:
        return parse_json_container(value, list)


PrepareRequest = Annotated[
    NativeTransferArgs | RawCallArgs | ContractCallArgs,
    Field(discriminator="kind"),
]
PREPARE_REQUEST_ADAPTER: TypeAdapter[PrepareRequest] = TypeAdapter(PrepareRequest)


class PrepareArgs(RootModel[PrepareRequest]):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "kind": "contract_call",
                    "contract_address": "0x" + "22" * 20,
                    "function_abi": {
                        "type": "function",
                        "name": "swap",
                        "inputs": [
                            {
                                "name": "request",
                                "type": "tuple",
                                "components": [
                                    {"name": "recipient", "type": "address"},
                                    {"name": "amountIn", "type": "uint256"},
                                ],
                            }
                        ],
                        "outputs": [{"name": "amountOut", "type": "uint256"}],
                        "stateMutability": "nonpayable",
                    },
                    "function_args": [["0x" + "33" * 20, 7]],
                }
            ]
        }
    )

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return inline_discriminated_branches(super().model_json_schema(*args, **kwargs))


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
    function_abi: FunctionAbi = Field(
        description="Copy exactly from blockchain.prepare_transaction."
    )
    function_args: list[Any] = Field(
        description=(
            "Copy exactly from blockchain.prepare_transaction and preserve JSON "
            "types; ABI integer arguments remain JSON numbers."
        )
    )
    function_signature: str = Field(
        min_length=1,
        description="Copy exactly from blockchain.prepare_transaction.",
    )


class SendTransactionArgs(ClosedModel):
    transaction: NormalizedTransaction = Field(
        description=(
            "Copy the exact transaction object from the structured "
            "blockchain.prepare_transaction result."
        )
    )
    call_context: CallContext | None = Field(
        description=(
            "Copy the exact call_context object from the structured "
            "blockchain.prepare_transaction result; preserve JSON types and use "
            "null for native transfers and raw calls."
        )
    )
    preparation_digest: PreparationDigest = Field(
        description="Copy exactly from blockchain.prepare_transaction."
    )

    @field_validator("transaction", "call_context", mode="before")
    @classmethod
    def parse_nested_object(cls, value: Any) -> Any:
        return parse_json_container(value, dict)


SEND_REQUEST_ADAPTER = TypeAdapter(SendTransactionArgs)


class SimulationResult(ClosedModel):
    state: Literal["succeeded"]
    chain_id: DecimalString
    block_identifier: str
    resolved_block_number: DecimalString | None
    resolved_block_hash: HexData | None
    return_data: HexData
    gas_estimate: DecimalString
    decoded_returns: list[Any] | None


class PreparedTransactionResult(ClosedModel):
    ok: Literal[True]
    state: Literal["prepared"]
    transaction: NormalizedTransaction
    call_context: CallContext | None
    simulation: SimulationResult
    preparation_digest: PreparationDigest
