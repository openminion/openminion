from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from dataclasses import asdict

import pytest
from eth_abi.exceptions import DecodingError
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3Exception

from openminion.tools.blockchain.runtime import prepare_transaction
from openminion.tools.blockchain.preparations import SessionRecordError
from openminion.tools.blockchain.confirmation import (
    build_blockchain_send_confirmation_preview,
    parse_blockchain_send_confirmation_preview,
)
from openminion.tools.blockchain.schema_types import FunctionAbi

PRIVATE_KEY = "0x" + "11" * 32
SENDER = Account.from_key(PRIVATE_KEY).address
RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


class _SecretService:
    def __init__(self, value: str = PRIVATE_KEY) -> None:
        self.value = value
        self.reads: list[tuple[str, str]] = []

    def get_secret_sync(self, key: str, *, namespace: str) -> str:
        self.reads.append((key, namespace))
        return self.value


def _context(
    *,
    secret_service: Any | None = None,
    max_total_fee_wei: str = "10000000000000000",
) -> SimpleNamespace:
    return SimpleNamespace(
        secret_service=secret_service,
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "runtime_tools": {
                        "blockchain": {
                            "enabled": True,
                            "rpc_url": "http://127.0.0.1:8545",
                            "chain_id": 31337,
                            "signer_secret_key": "signer-reference-sentinel",
                            "signer_secret_namespace": "chain-reference-sentinel",
                            "writes_enabled": False,
                            "max_total_fee_wei": max_total_fee_wei,
                            "receipt_timeout_seconds": 60,
                        }
                    }
                }
            }
        ),
    )


def test_configured_prepare_requires_the_optional_network_pair() -> None:
    secret_service = _SecretService()
    context = _context(secret_service=secret_service)
    blockchain = context.policy.raw["context_metadata"]["runtime_tools"]["blockchain"]
    blockchain.pop("rpc_url")
    blockchain.pop("chain_id")

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        context,
        web3=_Web3(),
    )

    assert result["error"] == {
        "code": "FEATURE_UNAVAILABLE",
        "message": "Configured blockchain network is unavailable.",
        "retryable": False,
        "details": {"feature": "configured_blockchain_network"},
    }
    assert secret_service.reads == []


class _Contract:
    @staticmethod
    def encode_abi(name: str, *, args: list[Any]) -> str:
        assert name == "setValue"
        assert args == [7]
        return "0x1234"


class _Eth:
    def __init__(self, *, legacy: bool = False) -> None:
        self.account = Account
        self.chain_id = 31337
        self.max_priority_fee = 3
        self.gas_price = 5
        self.legacy = legacy
        self.simulation_error: Exception | None = None
        self.estimated: list[dict[str, Any]] = []
        self.simulated: list[tuple[dict[str, Any], str]] = []
        self.signed = 0
        self.broadcast = 0
        self.estimate_error: Exception | None = None

    def contract(self, *, abi: list[dict[str, Any]]) -> _Contract:
        assert len(abi) == 1
        return _Contract()

    @staticmethod
    def get_transaction_count(address: str, block: str) -> int:
        assert address == SENDER
        assert block == "pending"
        return 9

    def estimate_gas(self, transaction: dict[str, Any]) -> int:
        self.estimated.append(transaction)
        if self.estimate_error is not None:
            raise self.estimate_error
        return 21000

    def get_block(self, block: str) -> dict[str, int]:
        assert block == "pending"
        return {} if self.legacy else {"baseFeePerGas": 10}

    def call(self, transaction: dict[str, Any], block: str) -> bytes:
        self.simulated.append((transaction, block))
        if self.simulation_error is not None:
            raise self.simulation_error
        return b""


class _Web3:
    def __init__(self, *, legacy: bool = False) -> None:
        self.eth = _Eth(legacy=legacy)
        self.codec = Web3().codec

    @staticmethod
    def to_checksum_address(value: str) -> str:
        return Web3.to_checksum_address(value)


@pytest.mark.parametrize(
    ("arguments", "expected_data", "has_call_context"),
    [
        (
            {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "4"},
            "0x",
            False,
        ),
        (
            {
                "kind": "raw_call",
                "to_address": RECIPIENT,
                "data": "0xabcd",
                "value_wei": "0",
            },
            "0xabcd",
            False,
        ),
        (
            {
                "kind": "contract_call",
                "contract_address": RECIPIENT,
                "function_abi": {
                    "type": "function",
                    "name": "setValue",
                    "inputs": [{"name": "value", "type": "uint256"}],
                    "outputs": [],
                    "stateMutability": "nonpayable",
                },
                "function_args": [7],
            },
            "0x1234",
            True,
        ),
    ],
)
def test_prepare_branches_are_deterministic_and_side_effect_free(
    arguments: dict[str, Any], expected_data: str, has_call_context: bool
) -> None:
    secret = _SecretService()
    client = _Web3()

    result = prepare_transaction(
        arguments,
        _context(secret_service=secret),
        web3=client,
    )

    assert result["ok"] is True
    assert result["state"] == "prepared"
    assert result["transaction"] == {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": SENDER,
        "to_address": RECIPIENT,
        "value_wei": arguments.get("value_wei", "0"),
        "nonce": "9",
        "gas_limit": "21000",
        "data": expected_data,
        "max_fee_per_gas_wei": "23",
        "max_priority_fee_per_gas_wei": "3",
        "max_total_fee_wei": "483000",
    }
    assert (result["call_context"] is not None) is has_call_context
    assert result["preparation_digest"].startswith("sha256:")
    assert result["simulation"] == {
        "state": "succeeded",
        "chain_id": "31337",
        "block_identifier": "pending",
        "resolved_block_number": None,
        "resolved_block_hash": None,
        "return_data": "0x",
        "gas_estimate": "21000",
        "decoded_returns": None,
    }
    assert secret.reads == [("signer-reference-sentinel", "chain-reference-sentinel")]
    assert client.eth.signed == 0
    assert client.eth.broadcast == 0


def test_prepare_uses_legacy_fee_when_pending_block_has_no_base_fee() -> None:
    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService()),
        web3=_Web3(legacy=True),
    )

    assert result["transaction"]["transaction_type"] == "legacy"
    assert result["transaction"]["gas_price_wei"] == "5"
    assert result["transaction"]["max_total_fee_wei"] == "105000"


def test_prepare_without_service_returns_sanitized_signer_error() -> None:
    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(),
        web3=_Web3(),
    )

    rendered = str(result)
    assert result["error"]["code"] == "SIGNER_UNAVAILABLE"
    assert "signer-reference-sentinel" not in rendered
    assert "chain-reference-sentinel" not in rendered
    assert PRIVATE_KEY not in rendered


def test_prepare_enforces_fee_cap_before_simulation() -> None:
    client = _Web3()
    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService(), max_total_fee_wei="1"),
        web3=client,
    )

    assert result["error"] == {
        "code": "FEE_CAP_EXCEEDED",
        "message": "Transaction fee exceeds the configured cap.",
        "retryable": False,
        "details": {
            "max_total_fee_wei": "483000",
            "configured_max_total_fee_wei": "1",
        },
    }
    assert client.eth.simulated == []


def test_prepare_separates_rpc_failure_without_provider_text() -> None:
    client = _Web3()
    client.eth.simulation_error = Web3Exception("private provider text")

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService()),
        web3=client,
    )

    assert result["error"]["code"] == "RPC_UNAVAILABLE"
    assert result["error"]["details"] == {"operation": "simulate"}
    assert "private provider text" not in str(result)


def test_prepare_returns_structured_standard_revert_without_provider_text() -> None:
    client = _Web3()
    data = "0x08c379a0" + Web3().codec.encode(["string"], ["minimum output"]).hex()
    client.eth.simulation_error = ContractLogicError("private provider text", data=data)

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService()),
        web3=client,
    )

    assert result["error"]["code"] == "SIMULATION_REVERTED"
    assert result["error"]["details"] == {
        "stage": "prepare",
        "revert": {
            "kind": "standard_error",
            "reason": "minimum output",
            "raw_data": data,
        },
        "broadcast_attempted": False,
    }
    assert "private provider text" not in str(result)


def test_prepare_keeps_estimation_revert_structured() -> None:
    client = _Web3()
    data = "0x08c379a0" + Web3().codec.encode(["string"], ["minimum output"]).hex()
    client.eth.estimate_error = ContractLogicError("private provider text", data=data)

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService()),
        web3=client,
    )

    assert result["error"]["code"] == "SIMULATION_REVERTED"
    assert result["error"]["details"]["revert"]["kind"] == "standard_error"
    assert result["error"]["details"]["broadcast_attempted"] is False
    assert "private provider text" not in str(result)


def test_prepare_rejects_chain_mismatch() -> None:
    client = _Web3()
    client.eth.chain_id = 1

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService()),
        web3=client,
    )

    assert result["error"]["code"] == "CHAIN_MISMATCH"
    assert result["error"]["details"] == {
        "expected_chain_id": 31337,
        "observed_chain_id": 1,
    }


def test_resolved_prepare_persists_digest_bound_v2_approval(
    monkeypatch,
) -> None:
    from openminion.tools.blockchain import resolved_calls

    context = _context(secret_service=_SecretService())
    blockchain = context.policy.raw["context_metadata"]["runtime_tools"]["blockchain"]
    blockchain.pop("rpc_url")
    blockchain.pop("chain_id")
    blockchain["confirmation_depth"] = 2
    context.session_id = "session"
    context.env = {}
    resolution_digest = "sha256:" + "12" * 32
    block_hash = "0x" + "34" * 32
    setter = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "setApr",
            "inputs": [{"name": "value", "type": "uint256"}],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    )
    setter_with_output = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "setApr",
            "inputs": [{"name": "value", "type": "uint256"}],
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "nonpayable",
        }
    )
    reader = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "apr",
            "inputs": [],
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
        }
    )
    record = {
        "resolution_digest": resolution_digest,
        "rpc_url": "https://rpc.example/",
        "sourcify_target_url": "https://sourcify.dev/target",
        "sourcify_target_match": "exact_match",
        "sourcify_implementation_url": None,
        "sourcify_implementation_match": None,
        "expected_chain_id": 31337,
        "observed_chain_id": 31337,
        "expected_genesis_hash": "0x" + "01" * 32,
        "observed_genesis_hash": "0x" + "01" * 32,
        "expected_checkpoint": None,
        "observed_checkpoint": None,
        "verification_block_number": "40",
        "verification_block_hash": "0x" + "02" * 32,
        "contract_address": RECIPIENT,
        "proxy_kind": "direct",
        "implementation_address": None,
        "abi_address": RECIPIENT,
        "target_code_hash": "0x" + "03" * 32,
        "implementation_code_hash": None,
    }
    saved: dict[str, Any] = {}

    monkeypatch.setattr(resolved_calls, "load_resolution", lambda digest, ctx: record)
    monkeypatch.setattr(
        resolved_calls,
        "revalidate_resolution",
        lambda value: {"block_number": "42", "block_hash": block_hash},
    )
    monkeypatch.setattr(
        resolved_calls,
        "function_by_signature",
        lambda value, signature: setter if signature.startswith("setApr") else reader,
    )

    rpc_state = {"balance": "0xde0b6b3a7640000"}

    def rpc_call(_record, method, params):
        if method == "eth_getBalance":
            assert params[1] == "latest"
        return {
            "eth_getBalance": rpc_state["balance"],
            "eth_getTransactionCount": "0x9",
            "eth_call": "0x",
            "eth_estimateGas": "0x5208",
            "eth_maxPriorityFeePerGas": "0x3",
        }.get(method) or {"number": "0x2a", "hash": block_hash, "baseFeePerGas": "0xa"}

    monkeypatch.setattr(resolved_calls, "rpc_call", rpc_call)
    monkeypatch.setattr(
        resolved_calls,
        "save_resolved_preparation_record",
        lambda record, context, **kwargs: saved.update(record),
    )

    result = prepare_transaction(
        {
            "kind": "resolved_contract_call",
            "resolution_digest": resolution_digest,
            "function_signature": "setApr(uint256)",
            "arguments": [5],
            "postconditions": [
                {
                    "function_signature": "apr()",
                    "arguments": [],
                    "expected_result": ["5"],
                }
            ],
        },
        context,
    )

    assert result["ok"] is True
    assert result["preparation_digest"] == saved["preparation_digest"]
    assert saved["resolved_context"]["rpc_origin"] == "https://rpc.example"
    assert saved["signer_address"] == SENDER
    preview = build_blockchain_send_confirmation_preview(saved)
    assert preview.schema_version == "blockchain-send-preview-v2"
    assert preview.signer_address == SENDER
    assert preview.postconditions[0]["expected_result"] == ["5"]
    assert parse_blockchain_send_confirmation_preview(asdict(preview)) == preview

    original_decode = resolved_calls.decode_abi_values

    def fail_decode(*_args, **_kwargs):
        raise DecodingError("invalid return data")

    monkeypatch.setattr(resolved_calls, "decode_abi_values", fail_decode)
    monkeypatch.setattr(
        resolved_calls,
        "function_by_signature",
        lambda value, signature: (
            setter_with_output if signature.startswith("setApr") else reader
        ),
    )
    undecoded = prepare_transaction(
        {
            "kind": "resolved_contract_call",
            "resolution_digest": resolution_digest,
            "function_signature": "setApr(uint256)",
            "arguments": [5],
        },
        context,
    )
    assert undecoded["ok"] is True
    assert undecoded["simulation"]["decoded_returns"] is None
    monkeypatch.setattr(resolved_calls, "decode_abi_values", original_decode)
    monkeypatch.setattr(
        resolved_calls,
        "function_by_signature",
        lambda value, signature: setter if signature.startswith("setApr") else reader,
    )

    rpc_state["balance"] = "0x1"
    insufficient = prepare_transaction(
        {
            "kind": "resolved_contract_call",
            "resolution_digest": resolution_digest,
            "function_signature": "setApr(uint256)",
            "arguments": [5],
        },
        context,
    )
    assert insufficient["error"]["code"] == "INSUFFICIENT_FUNDS"

    rpc_state["balance"] = "0xde0b6b3a7640000"

    def fail_save(*_args, **_kwargs):
        raise SessionRecordError("unavailable", reason="unavailable")

    monkeypatch.setattr(resolved_calls, "save_resolved_preparation_record", fail_save)
    unavailable = prepare_transaction(
        {
            "kind": "resolved_contract_call",
            "resolution_digest": resolution_digest,
            "function_signature": "setApr(uint256)",
            "arguments": [5],
        },
        context,
    )
    assert unavailable["error"] == {
        "code": "INVALID_ARGUMENT",
        "message": "Resolved preparation could not be stored for this session.",
        "retryable": False,
        "details": {"reason": "unavailable"},
    }
