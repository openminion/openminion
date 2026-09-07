from __future__ import annotations

import re
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
    ErrorAbi,
    EventAbi,
    FunctionAbi,
    HexData,
    TransactionHash,
    inline_discriminated_branches,
    parse_json_container,
)


class SimulateCallArgs(ClosedModel):
    action: Literal["simulate_call"] = Field(
        description=(
            "Use exactly simulate_call when asked to execute or simulate calldata "
            "against the configured chain and return its result or structured revert. "
            "Do not use for a contract read supplied as a function ABI and arguments; "
            "use blockchain.inspect."
        )
    )
    from_address: Address
    to_address: Address
    data: HexData
    value_wei: DecimalString = "0"
    block_identifier: str = "pending"
    function_abi: FunctionAbi | None = Field(
        default=None,
        description="JSON object; a strict JSON object string is also accepted.",
    )
    function_args: list[object] | None = Field(
        default=None,
        description=(
            "JSON array in ABI input order; a strict JSON array string is also "
            "accepted."
        ),
    )
    error_abis: list[ErrorAbi] = Field(
        default_factory=list,
        description=(
            "JSON array of error ABI objects; a strict JSON array string is also "
            "accepted."
        ),
    )

    @field_validator("block_identifier")
    @classmethod
    def validate_block_identifier(cls, value: str) -> str:
        if value in {"latest", "pending", "safe", "finalized"} or re.fullmatch(
            r"(?:0|[1-9][0-9]*)", value
        ):
            return value
        raise ValueError("unsupported block identifier")

    @model_validator(mode="after")
    def validate_function_pair(self) -> SimulateCallArgs:
        if (self.function_abi is None) != (self.function_args is None):
            raise ValueError("function ABI and arguments must be supplied together")
        return self

    @field_validator("function_abi", mode="before")
    @classmethod
    def parse_function_abi(cls, value: object) -> object:
        return parse_json_container(value, dict)

    @field_validator("function_args", "error_abis", mode="before")
    @classmethod
    def parse_arrays(cls, value: object) -> object:
        return parse_json_container(value, list)


class DecodeCalldataArgs(ClosedModel):
    action: Literal["decode_calldata"] = Field(
        description="Parse calldata without executing or simulating the call."
    )
    function_abi: FunctionAbi = Field(
        description="JSON object; a strict JSON object string is also accepted."
    )
    data: HexData

    @field_validator("function_abi", mode="before")
    @classmethod
    def parse_function_abi(cls, value: object) -> object:
        return parse_json_container(value, dict)


class DecodeRevertArgs(ClosedModel):
    action: Literal["decode_revert"] = Field(
        description=(
            "Use only to parse bytes already returned as revert data without executing "
            "a call. Do not use for transaction calldata that must be simulated."
        )
    )
    data: HexData
    error_abis: list[ErrorAbi] = Field(
        default_factory=list,
        description=(
            "JSON array of error ABI objects; a strict JSON array string is also "
            "accepted."
        ),
    )

    @field_validator("error_abis", mode="before")
    @classmethod
    def parse_error_abis(cls, value: object) -> object:
        return parse_json_container(value, list)


class TransactionEventsArgs(ClosedModel):
    action: Literal["transaction_events"] = Field(
        description=(
            "Decode one event ABI from one known transaction receipt. This does not "
            "verify receipt status; when both receipt status and events are requested "
            "in order, first use blockchain.inspect with action receipt, then use "
            "this action."
        )
    )
    transaction_hash: TransactionHash
    event_abi: EventAbi = Field(
        description="JSON object; a strict JSON object string is also accepted."
    )
    contract_address: Address | None = None

    @field_validator("event_abi", mode="before")
    @classmethod
    def parse_event_abi(cls, value: object) -> object:
        return parse_json_container(value, dict)


DebugRequest = Annotated[
    SimulateCallArgs | DecodeCalldataArgs | DecodeRevertArgs | TransactionEventsArgs,
    Field(discriminator="action"),
]
DEBUG_REQUEST_ADAPTER: TypeAdapter[DebugRequest] = TypeAdapter(DebugRequest)


class DebugArgs(RootModel[DebugRequest]):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "action": "simulate_call",
                    "from_address": "0x" + "11" * 20,
                    "to_address": "0x" + "22" * 20,
                    "data": "0x",
                    "value_wei": "0",
                    "block_identifier": "pending",
                },
                {
                    "action": "decode_calldata",
                    "function_abi": {
                        "type": "function",
                        "name": "balanceOf",
                        "inputs": [{"name": "owner", "type": "address"}],
                        "outputs": [{"name": "", "type": "uint256"}],
                        "stateMutability": "view",
                    },
                    "data": ("0x70a08231000000000000000000000000" + "11" * 20),
                },
                {"action": "decode_revert", "data": "0x08c379a0"},
                {
                    "action": "transaction_events",
                    "transaction_hash": "0x" + "aa" * 32,
                    "event_abi": {
                        "type": "event",
                        "name": "Swap",
                        "inputs": [
                            {
                                "name": "sender",
                                "type": "address",
                                "indexed": True,
                            },
                            {
                                "name": "amountOut",
                                "type": "uint256",
                                "indexed": False,
                            },
                        ],
                        "anonymous": False,
                    },
                },
            ]
        }
    )

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return inline_discriminated_branches(super().model_json_schema(*args, **kwargs))
