from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import hashlib

import pytest
from pydantic import ValidationError
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound, Web3Exception

from openminion.base.config.runtime.tools import (
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
)
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.blockchain.abi import encode_function_call, event_topic
from openminion.tools.blockchain.debug import debug_blockchain
from openminion.tools.blockchain.debug_results import DEBUG_RESULT_ADAPTER
from openminion.tools.blockchain.schema_types import EventAbi, FunctionAbi

ADDRESS = Web3.to_checksum_address("0x" + "11" * 20)
OTHER = Web3.to_checksum_address("0x" + "22" * 20)
TX_HASH = "0x" + "aa" * 32


class _SecretService:
    def get_secret_sync(self, *_args, **_kwargs):
        raise AssertionError("debug must not resolve a signing secret")


class _Eth:
    chain_id = 31337

    def __init__(self) -> None:
        self.call_result = b""
        self.call_error: Exception | None = None
        self.estimate = 21000
        self.transaction: dict[str, Any] | None = {"hash": TX_HASH}
        self.receipt: dict[str, Any] | None = None

    def call(self, _transaction: dict[str, Any], _block: str | int) -> bytes:
        if self.call_error is not None:
            raise self.call_error
        return self.call_result

    def estimate_gas(self, _transaction: dict[str, Any], _block: str | int) -> int:
        return self.estimate

    @staticmethod
    def contract(*, abi: list[dict[str, Any]]):
        return Web3().eth.contract(abi=abi)

    @staticmethod
    def get_block(block: str | int) -> dict[str, Any]:
        assert block != "pending"
        return {"number": 7, "hash": bytes.fromhex("33" * 32)}

    def get_transaction(self, _transaction_hash: str) -> dict[str, Any]:
        if self.transaction is None:
            raise TransactionNotFound("missing")
        return self.transaction

    def get_transaction_receipt(self, _transaction_hash: str) -> dict[str, Any]:
        if self.receipt is None:
            raise TransactionNotFound("pending")
        return self.receipt


class _Web3:
    def __init__(self) -> None:
        self.eth = _Eth()
        self.codec = Web3().codec

    @staticmethod
    def to_checksum_address(value: str) -> str:
        return Web3.to_checksum_address(value)

    @staticmethod
    def keccak(*, text: str):
        return Web3.keccak(text=text)


class _UnavailableChainId:
    @property
    def chain_id(self) -> int:
        raise Web3Exception("private provider text")


class _UnavailableWeb3(_Web3):
    def __init__(self) -> None:
        super().__init__()
        self.eth = _UnavailableChainId()


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        secret_service=_SecretService(),
        policy_authorization=None,
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "runtime_tools": {
                        "blockchain": {
                            "enabled": True,
                            "rpc_url": "http://127.0.0.1:8545",
                            "chain_id": 31337,
                            "writes_enabled": False,
                        }
                    }
                }
            }
        ),
    )


def _function() -> FunctionAbi:
    return FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "quote",
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
            "stateMutability": "view",
        }
    )


def _event() -> EventAbi:
    return EventAbi.model_validate(
        {
            "type": "event",
            "name": "Swap",
            "inputs": [
                {"name": "sender", "type": "address", "indexed": True},
                {"name": "amountOut", "type": "uint256", "indexed": False},
            ],
            "anonymous": False,
        }
    )


def test_debug_decode_calldata_preserves_ordered_tuple_values() -> None:
    client = _Web3()
    function = _function()
    data = encode_function_call(client, function, [[ADDRESS, 7]])

    result = debug_blockchain(
        {
            "action": "decode_calldata",
            "function_abi": function.model_dump(mode="json"),
            "data": data,
        },
        _context(),
        web3=client,
    )

    assert result == {
        "ok": True,
        "state": "succeeded",
        "action": "decode_calldata",
        "function_signature": "quote((address,uint256))",
        "arguments": [[ADDRESS, "7"]],
    }


def test_debug_local_decoders_do_not_require_rpc() -> None:
    function = _function()
    data = encode_function_call(Web3(), function, [[ADDRESS, 7]])

    calldata = debug_blockchain(
        {
            "action": "decode_calldata",
            "function_abi": function.model_dump(mode="json"),
            "data": data,
        },
        _context(),
    )
    revert = debug_blockchain(
        {"action": "decode_revert", "data": "0x"},
        _context(),
    )

    assert calldata["arguments"] == [[ADDRESS, "7"]]
    assert revert["revert"] == {"kind": "unknown", "raw_data": "0x"}


def test_debug_chain_id_failure_uses_action_specific_rpc_operation() -> None:
    simulation = debug_blockchain(
        {
            "action": "simulate_call",
            "from_address": ADDRESS,
            "to_address": OTHER,
            "data": "0x",
        },
        _context(),
        web3=_UnavailableWeb3(),
    )
    events = debug_blockchain(
        {
            "action": "transaction_events",
            "transaction_hash": TX_HASH,
            "event_abi": _event().model_dump(mode="json"),
        },
        _context(),
        web3=_UnavailableWeb3(),
    )

    assert simulation["error"]["details"] == {"operation": "simulate"}
    assert events["error"]["details"] == {"operation": "receipt"}


def test_debug_decodes_standard_panic_custom_and_unknown_reverts() -> None:
    client = _Web3()
    standard = "0x08c379a0" + client.codec.encode(["string"], ["minimum"]).hex()
    panic = "0x4e487b71" + client.codec.encode(["uint256"], [17]).hex()
    custom_abi = {
        "type": "error",
        "name": "MinimumOutput",
        "inputs": [{"name": "minimum", "type": "uint256"}],
    }
    custom = "0x" + Web3.keccak(text="MinimumOutput(uint256)")[:4].hex()
    custom += client.codec.encode(["uint256"], [9]).hex()

    results = [
        debug_blockchain(
            {"action": "decode_revert", "data": standard},
            _context(),
            web3=client,
        )["revert"],
        debug_blockchain(
            {"action": "decode_revert", "data": panic},
            _context(),
            web3=client,
        )["revert"],
        debug_blockchain(
            {
                "action": "decode_revert",
                "data": custom,
                "error_abis": [custom_abi],
            },
            _context(),
            web3=client,
        )["revert"],
        debug_blockchain(
            {"action": "decode_revert", "data": "0x"},
            _context(),
            web3=client,
        )["revert"],
    ]

    assert results[0] == {
        "kind": "standard_error",
        "reason": "minimum",
        "raw_data": standard,
    }
    assert results[1] == {"kind": "panic", "code": "17", "raw_data": panic}
    assert results[2] == {
        "kind": "custom_error",
        "signature": "MinimumOutput(uint256)",
        "arguments": ["9"],
        "raw_data": custom,
    }
    assert results[3] == {"kind": "unknown", "raw_data": "0x"}


def test_debug_simulation_success_and_structured_no_data_revert() -> None:
    client = _Web3()
    client.eth.call_result = client.codec.encode(["uint256"], [14])
    function = _function()
    data = encode_function_call(client, function, [[ADDRESS, 7]])
    success = debug_blockchain(
        {
            "action": "simulate_call",
            "from_address": ADDRESS,
            "to_address": OTHER,
            "data": data,
            "function_abi": function.model_dump(mode="json"),
            "function_args": [[ADDRESS, 7]],
            "block_identifier": "latest",
        },
        _context(),
        web3=client,
    )

    assert success["data"]["decoded_returns"] == ["14"]
    assert success["data"]["resolved_block_number"] == "7"
    assert success["data"]["resolved_block_hash"] == "0x" + "33" * 32

    client.eth.call_error = ContractLogicError("private provider text")
    reverted = debug_blockchain(
        {
            "action": "simulate_call",
            "from_address": ADDRESS,
            "to_address": OTHER,
            "data": "0x",
        },
        _context(),
        web3=client,
    )
    assert reverted["error"]["code"] == "SIMULATION_REVERTED"
    assert reverted["error"]["details"]["revert"] == {
        "kind": "data_unavailable",
        "raw_data": None,
    }
    assert "private provider text" not in str(reverted)


def test_debug_separates_transport_failure_from_revert() -> None:
    client = _Web3()
    client.eth.call_error = Web3Exception("private provider text")

    result = debug_blockchain(
        {
            "action": "simulate_call",
            "from_address": ADDRESS,
            "to_address": OTHER,
            "data": "0x",
        },
        _context(),
        web3=client,
    )

    assert result["error"]["code"] == "RPC_UNAVAILABLE"
    assert result["error"]["details"] == {"operation": "simulate"}
    assert "private provider text" not in str(result)


def test_transaction_events_distinguishes_unknown_pending_and_decodes_receipt() -> None:
    client = _Web3()
    event_abi = _event()
    base = {
        "action": "transaction_events",
        "transaction_hash": TX_HASH,
        "event_abi": event_abi.model_dump(mode="json"),
    }
    client.eth.transaction = None
    unknown = debug_blockchain(base, _context(), web3=client)
    assert unknown["error"]["code"] == "TRANSACTION_NOT_FOUND"

    client.eth.transaction = {"hash": TX_HASH}
    pending = debug_blockchain(base, _context(), web3=client)
    assert pending["error"]["code"] == "RECEIPT_PENDING"
    assert pending["error"]["details"]["accepted"] is True

    indexed_address = "0x" + client.codec.encode(["address"], [ADDRESS]).hex()
    client.eth.receipt = {
        "blockNumber": 8,
        "logs": [
            {
                "address": OTHER,
                "logIndex": 3,
                "topics": [event_topic(event_abi, client), indexed_address],
                "data": "0x" + client.codec.encode(["uint256"], [14]).hex(),
            }
        ],
    }
    result = debug_blockchain(base, _context(), web3=client)

    assert result["event_signature"] == "Swap(address,uint256)"
    assert result["events"] == [
        {
            "contract_address": OTHER,
            "log_index": "3",
            "arguments": [
                {
                    "name": "sender",
                    "indexed": True,
                    "value_kind": "value",
                    "value": ADDRESS,
                },
                {
                    "name": "amountOut",
                    "indexed": False,
                    "value_kind": "value",
                    "value": "14",
                },
            ],
        }
    ]


def test_transaction_event_limit_is_not_silently_truncated() -> None:
    client = _Web3()
    event_abi = _event()
    client.eth.receipt = {
        "blockNumber": 8,
        "logs": [
            {
                "address": OTHER,
                "logIndex": index,
                "topics": [event_topic(event_abi, client)],
                "data": "0x",
            }
            for index in range(101)
        ],
    }

    result = debug_blockchain(
        {
            "action": "transaction_events",
            "transaction_hash": TX_HASH,
            "event_abi": event_abi.model_dump(mode="json"),
        },
        _context(),
        web3=client,
    )

    assert result["error"]["code"] == "RESULT_LIMIT_EXCEEDED"
    assert result["error"]["details"] == {
        "surface": "transaction_events",
        "limit": 100,
        "observed_at_least": 101,
    }


def test_debug_result_contract_rejects_unknown_fields_and_mismatched_errors() -> None:
    success = debug_blockchain(
        {"action": "decode_revert", "data": "0x"},
        _context(),
    )
    assert DEBUG_RESULT_ADAPTER.validate_python(success)

    invalid = {**success, "unexpected": True}
    with pytest.raises(ValidationError):
        DEBUG_RESULT_ADAPTER.validate_python(invalid)
    with pytest.raises(ValidationError):
        DEBUG_RESULT_ADAPTER.validate_python(
            {
                "ok": False,
                "state": "failed",
                "error": {
                    "code": "RPC_UNAVAILABLE",
                    "message": "Blockchain RPC operation failed.",
                    "retryable": False,
                    "details": {"operation": "decode_revert"},
                },
            }
        )


def test_registered_direct_debug_invokes_all_four_actions(
    tmp_path, monkeypatch
) -> None:
    client = _Web3()
    event_abi = _event()
    indexed_address = "0x" + client.codec.encode(["address"], [ADDRESS]).hex()
    client.eth.receipt = {
        "blockNumber": 8,
        "logs": [
            {
                "address": OTHER,
                "logIndex": 3,
                "topics": [event_topic(event_abi, client), indexed_address],
                "data": "0x" + client.codec.encode(["uint256"], [14]).hex(),
            }
        ],
    }
    monkeypatch.setattr(
        "openminion.tools.blockchain.debug._client", lambda _url: client
    )
    bootstrap = build_runtime_bootstrap(
        config=SimpleNamespace(
            runtime=SimpleNamespace(
                tools=ToolRuntimeConfig(
                    blockchain=BlockchainToolRuntimeConfig(
                        enabled=True,
                        rpc_url="http://127.0.0.1:1",
                        chain_id=31337,
                    )
                )
            ),
            mcp_servers=None,
            tool_selection=None,
        ),
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )
    function = _function()
    calldata = encode_function_call(client, function, [[ADDRESS, 7]])
    arguments = [
        {
            "action": "simulate_call",
            "from_address": ADDRESS,
            "to_address": OTHER,
            "data": "0x",
        },
        {
            "action": "decode_calldata",
            "function_abi": function.model_dump(mode="json"),
            "data": calldata,
        },
        {"action": "decode_revert", "data": "0x"},
        {
            "action": "transaction_events",
            "transaction_hash": TX_HASH,
            "event_abi": event_abi.model_dump(mode="json"),
        },
    ]
    context = ToolExecutionContext(
        channel="test",
        target="test",
        session_id="session",
        metadata={
            "runtime_tools": {
                "blockchain": {
                    "enabled": True,
                    "rpc_url": "http://127.0.0.1:1",
                    "chain_id": 31337,
                    "writes_enabled": False,
                }
            }
        },
    )
    tool = bootstrap.registry.get("blockchain.debug")

    results = [
        execute_tool_spec_call(tool=tool, arguments=item, context=context)
        for item in arguments
    ]

    assert all(result.ok for result in results)


def test_debug_rejects_unknown_action_and_never_resolves_secret() -> None:
    result = debug_blockchain(
        {"action": "arbitrary_rpc", "method": "eth_sendTransaction"},
        _context(),
        web3=_Web3(),
    )

    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_debug_request_limit_rejects_without_copying_raw_data() -> None:
    result = debug_blockchain(
        {"action": "decode_revert", "data": "0x" + "aa" * 33000},
        _context(),
        web3=_Web3(),
    )

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["error"]["details"] == {
        "field": "",
        "reason": "debug_request_size",
    }
    assert "aa" * 100 not in str(result)


def test_debug_result_limit_replaces_large_decoded_revert() -> None:
    client = _Web3()
    reason = "x" * 25000
    data = "0x08c379a0" + client.codec.encode(["string"], [reason]).hex()

    result = debug_blockchain(
        {"action": "decode_revert", "data": data},
        _context(),
        web3=client,
    )

    assert result["error"]["code"] == "RESULT_LIMIT_EXCEEDED"
    assert result["error"]["details"]["surface"] == "debug_result"
    assert result["error"]["details"]["limit"] == 65536
    assert reason[:100] not in str(result)


def test_debug_result_limit_replaces_large_provider_revert() -> None:
    client = _Web3()
    reason = "provider-value" * 2200
    data = "0x08c379a0" + client.codec.encode(["string"], [reason]).hex()
    client.eth.call_error = ContractLogicError("private provider text", data=data)

    result = debug_blockchain(
        {
            "action": "simulate_call",
            "from_address": ADDRESS,
            "to_address": OTHER,
            "data": "0x",
        },
        _context(),
        web3=client,
    )

    assert result["error"]["code"] == "RESULT_LIMIT_EXCEEDED"
    assert result["error"]["details"]["surface"] == "debug_result"
    assert "private provider text" not in str(result)
    assert reason[:100] not in str(result)


def test_event_decode_failure_identifies_the_exact_log() -> None:
    client = _Web3()
    event_abi = _event()
    indexed_address = "0x" + client.codec.encode(["address"], [ADDRESS]).hex()
    client.eth.receipt = {
        "blockNumber": 8,
        "logs": [
            {
                "address": OTHER,
                "logIndex": 9,
                "topics": [event_topic(event_abi, client), indexed_address],
                "data": "0x01",
            }
        ],
    }

    result = debug_blockchain(
        {
            "action": "transaction_events",
            "transaction_hash": TX_HASH,
            "event_abi": event_abi.model_dump(mode="json"),
        },
        _context(),
        web3=client,
    )

    assert result["error"]["code"] == "ABI_DECODE_FAILED"
    assert result["error"]["details"] == {
        "operation": "transaction_events",
        "data_sha256": hashlib.sha256(b"\x01").hexdigest(),
        "log_index": "9",
    }
