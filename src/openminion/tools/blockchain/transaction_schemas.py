from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    field_validator,
    model_validator,
)

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


class EqualityPostcondition(ClosedModel):
    function_signature: str = Field(
        min_length=1,
        description=(
            "Copy one exact read-only canonical signature from the resolver's "
            "available_function_signatures list."
        ),
    )
    arguments: list[Any]
    expected_result: list[Any]

    @field_validator("arguments", "expected_result", mode="before")
    @classmethod
    def parse_json_array(cls, value: Any) -> Any:
        return parse_json_container(value, list)


class ResolvedContractCallArgs(ClosedModel):
    kind: Literal["resolved_contract_call"] = Field(
        description="Required discriminator; use exactly resolved_contract_call."
    )
    resolution_digest: PreparationDigest
    function_signature: str = Field(
        min_length=1,
        description=(
            "Copy one exact canonical signature from the resolver's "
            "available_function_signatures list; do not include names or return types."
        ),
    )
    arguments: list[Any]
    value_wei: DecimalString = "0"
    postconditions: list[EqualityPostcondition] = Field(
        default_factory=list,
        max_length=5,
    )

    @field_validator("arguments", "postconditions", mode="before")
    @classmethod
    def parse_json_array(cls, value: Any) -> Any:
        return parse_json_container(value, list)


PrepareRequest = Annotated[
    NativeTransferArgs | RawCallArgs | ContractCallArgs | ResolvedContractCallArgs,
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


class SendPreparedTransactionArgs(ClosedModel):
    preparation_digest: PreparationDigest | None = Field(
        default=None,
        description=(
            "Select a preparation by digest. Omit this field to use the latest "
            "preparation in the current session."
        ),
    )


class SimulationResult(ClosedModel):
    state: Literal["succeeded"]
    chain_id: DecimalString
    block_identifier: str
    resolved_block_number: DecimalString | None
    resolved_block_hash: HexData | None
    return_data: HexData
    gas_estimate: DecimalString
    decoded_returns: list[Any] | None


class ResolvedCheckpoint(ClosedModel):
    block_number: DecimalString
    block_hash: HexData


class ResolvedContext(ClosedModel):
    resolution_digest: PreparationDigest
    rpc_origin: str
    sourcify_target_origin: str
    sourcify_implementation_origin: str | None
    expected_chain_id: int = Field(ge=1)
    observed_chain_id: int = Field(ge=1)
    expected_genesis_hash: HexData
    observed_genesis_hash: HexData
    expected_checkpoint: ResolvedCheckpoint | None
    observed_checkpoint: ResolvedCheckpoint | None
    resolution_block_number: DecimalString
    resolution_block_hash: HexData
    preparation_block_number: DecimalString
    preparation_block_hash: HexData
    contract_address: Address
    proxy_kind: Literal["direct", "eip1967"]
    implementation_address: Address | None
    abi_address: Address
    target_code_hash: HexData
    implementation_code_hash: HexData | None
    function_abi: FunctionAbi


class ResolvedPreparedTransaction(ClosedModel):
    schema_version: Literal[2]
    kind: Literal["resolved_contract_call"]
    resolution_digest: PreparationDigest
    resolved_context: ResolvedContext
    transaction: NormalizedTransaction
    call_context: CallContext
    simulation: SimulationResult
    signer_address: Address
    postconditions: list[EqualityPostcondition] = Field(max_length=5)
    preparation_digest: PreparationDigest

    @model_validator(mode="after")
    def validate_bound_facts(self) -> ResolvedPreparedTransaction:
        if (
            self.resolution_digest != self.resolved_context.resolution_digest
            or self.signer_address != self.transaction.from_address
            or self.transaction.to_address != self.resolved_context.contract_address
            or self.call_context.function_abi != self.resolved_context.function_abi
            or self.simulation.chain_id != str(self.transaction.chain_id)
            or self.simulation.resolved_block_number
            != self.resolved_context.preparation_block_number
            or self.simulation.resolved_block_hash
            != self.resolved_context.preparation_block_hash
        ):
            raise ValueError("resolved preparation facts do not match")
        return self


RESOLVED_PREPARATION_ADAPTER = TypeAdapter(ResolvedPreparedTransaction)


class PostconditionResult(EqualityPostcondition):
    actual_result: list[Any] | None
    matched: bool
    error_code: str | None


OperationState = Literal[
    "broadcast_unknown",
    "pending",
    "confirming",
    "succeeded",
    "reverted",
    "reorged",
    "postcondition_failed",
]


class OperationRecord(ClosedModel):
    schema_version: Literal[1]
    preparation_digest: PreparationDigest
    resolution_digest: PreparationDigest
    operation_digest: PreparationDigest
    transaction_hash: TransactionHash
    submission_started: Literal[True]
    broadcast_attempts: Literal[1]
    state: OperationState
    receipt_block_number: DecimalString | None
    receipt_block_hash: HexData | None
    receipt_status: Literal[0, 1] | None
    confirmations: int = Field(ge=0)
    confirmation_depth: int = Field(ge=1, le=64)
    gas_used: DecimalString | None = None
    effective_gas_price_wei: DecimalString | None = None
    postconditions: list[EqualityPostcondition] = Field(max_length=5)
    postcondition_results: list[PostconditionResult] = Field(
        default_factory=list,
        max_length=5,
    )
    last_error_code: str | None


OPERATION_RECORD_ADAPTER = TypeAdapter(OperationRecord)


class PreparedTransactionResult(ClosedModel):
    ok: Literal[True]
    state: Literal["prepared"]
    transaction: NormalizedTransaction
    call_context: CallContext | None
    simulation: SimulationResult
    preparation_digest: PreparationDigest


class ResolvedPreparedTransactionResult(ResolvedPreparedTransaction):
    ok: Literal[True]
    state: Literal["prepared"]


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def preparation_digest(
    transaction: Mapping[str, Any],
    call_context: Mapping[str, Any] | None,
) -> str:
    return _canonical_digest(
        {"transaction": dict(transaction), "call_context": call_context}
    )


def resolved_preparation_digest(record: Mapping[str, Any]) -> str:
    normalized = dict(record)
    normalized.pop("preparation_digest", None)
    return _canonical_digest(normalized)


def validate_resolved_preparation_record(
    record: Mapping[str, Any],
) -> dict[str, Any]:
    return RESOLVED_PREPARATION_ADAPTER.validate_python(record).model_dump(mode="json")


def operation_digest(record: Mapping[str, Any]) -> str:
    immutable_fields = {
        key: record[key]
        for key in (
            "schema_version",
            "preparation_digest",
            "resolution_digest",
            "transaction_hash",
            "submission_started",
            "broadcast_attempts",
            "confirmation_depth",
            "postconditions",
        )
    }
    return _canonical_digest(immutable_fields)


def validate_operation_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return OPERATION_RECORD_ADAPTER.validate_python(record).model_dump(mode="json")
