from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import Web3Exception

from openminion.tools.blockchain.runtime import prepare_transaction

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
        self.simulation_error = False
        self.estimated: list[dict[str, Any]] = []
        self.simulated: list[tuple[dict[str, Any], str]] = []
        self.signed = 0
        self.broadcast = 0

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
        return 21000

    def get_block(self, block: str) -> dict[str, int]:
        assert block == "pending"
        return {} if self.legacy else {"baseFeePerGas": 10}

    def call(self, transaction: dict[str, Any], block: str) -> bytes:
        self.simulated.append((transaction, block))
        if self.simulation_error:
            raise Web3Exception("private provider text")
        return b""


class _Web3:
    def __init__(self, *, legacy: bool = False) -> None:
        self.eth = _Eth(legacy=legacy)

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


def test_prepare_normalizes_simulation_failure_without_provider_text() -> None:
    client = _Web3()
    client.eth.simulation_error = True

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(secret_service=_SecretService()),
        web3=client,
    )

    assert result["error"]["code"] == "SIMULATION_REVERTED"
    assert result["error"]["details"] == {"stage": "prepare"}
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
