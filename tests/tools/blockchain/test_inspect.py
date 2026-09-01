from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from web3 import Web3
from web3.exceptions import TransactionNotFound

from openminion.tools.blockchain.runtime import inspect_blockchain

ADDRESS = Web3.to_checksum_address("0x" + "11" * 20)
OTHER_ADDRESS = Web3.to_checksum_address("0x" + "22" * 20)
TX_HASH = "0x" + "ab" * 32


def _context(*, chain_id: int = 31337) -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "runtime_tools": {
                        "blockchain": {
                            "enabled": True,
                            "rpc_url": "http://127.0.0.1:8545",
                            "chain_id": chain_id,
                            "signer_secret_key": "",
                            "signer_secret_namespace": "blockchain",
                            "writes_enabled": False,
                            "max_total_fee_wei": "10000000000000000",
                            "receipt_timeout_seconds": 60,
                        }
                    }
                }
            }
        )
    )


class _FunctionCall:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.block_identifier = None

    def call(self, *, block_identifier: str) -> Any:
        self.block_identifier = block_identifier
        return self.result


class _FunctionFactory:
    def __init__(self, result: Any) -> None:
        self.result = result

    def __call__(self, *_args: Any) -> _FunctionCall:
        return _FunctionCall(self.result)


class _Contract:
    def __init__(self, result: Any) -> None:
        self.result = result

    def get_function_by_name(self, _name: str) -> _FunctionFactory:
        return _FunctionFactory(self.result)


class _Eth:
    def __init__(self) -> None:
        self.chain_id = 31337
        self.block_number = 42
        self.balance = 123
        self.code = bytes.fromhex("6000")
        self.contract_result: Any = 7
        self.receipt: dict[str, Any] | None = {
            "status": 1,
            "blockNumber": 41,
            "gasUsed": 21000,
            "effectiveGasPrice": 2,
        }

    def get_balance(self, _address: str) -> int:
        return self.balance

    def get_code(self, _address: str) -> bytes:
        return self.code

    def contract(self, *, address: str, abi: list[dict[str, Any]]) -> _Contract:
        assert address == ADDRESS
        assert len(abi) == 1
        return _Contract(self.contract_result)

    def get_transaction(self, _transaction_hash: str) -> dict[str, Any]:
        return {
            "from": ADDRESS,
            "to": OTHER_ADDRESS,
            "value": 5,
            "input": bytes.fromhex("1234"),
            "nonce": 9,
            "blockNumber": 40,
        }

    def get_transaction_receipt(self, _transaction_hash: str) -> dict[str, Any]:
        if self.receipt is None:
            raise TransactionNotFound("transaction not found")
        return self.receipt


class _Web3:
    def __init__(self) -> None:
        self.eth = _Eth()

    @staticmethod
    def to_checksum_address(value: str) -> str:
        return Web3.to_checksum_address(value)


class _FailingEth:
    @property
    def chain_id(self) -> int:
        raise OSError


class _FailingWeb3:
    eth = _FailingEth()


def test_chain_summary_returns_grounded_decimal_facts() -> None:
    result = inspect_blockchain(
        {"action": "chain_summary"}, _context(), web3=_Web3()
    )

    assert result == {
        "ok": True,
        "state": "succeeded",
        "action": "chain_summary",
        "data": {"chain_id": 31337, "latest_block_number": "42"},
    }


def test_native_balance_and_bytecode_are_read_only() -> None:
    client = _Web3()

    balance = inspect_blockchain(
        {"action": "native_balance", "address": ADDRESS},
        _context(),
        web3=client,
    )
    bytecode = inspect_blockchain(
        {"action": "bytecode", "address": ADDRESS},
        _context(),
        web3=client,
    )

    assert balance["data"] == {
        "chain_id": 31337,
        "address": ADDRESS,
        "balance_wei": "123",
    }
    assert bytecode["data"] == {
        "chain_id": 31337,
        "address": ADDRESS,
        "has_code": True,
        "bytecode": "0x6000",
    }


def test_contract_read_recursively_normalizes_many_outputs() -> None:
    client = _Web3()
    client.eth.contract_result = (
        ADDRESS,
        bytes.fromhex("1234"),
        7,
        True,
        "hello",
        [[1, 2], [3]],
    )
    abi = {
        "type": "function",
        "name": "inspectValues",
        "inputs": [],
        "outputs": [
            {"name": "owner", "type": "address"},
            {"name": "blob", "type": "bytes"},
            {"name": "count", "type": "uint256"},
            {"name": "enabled", "type": "bool"},
            {"name": "label", "type": "string"},
            {"name": "matrix", "type": "uint8[][]"},
        ],
        "stateMutability": "view",
    }

    result = inspect_blockchain(
        {
            "action": "contract_read",
            "contract_address": ADDRESS,
            "function_abi": abi,
            "function_args": [],
        },
        _context(),
        web3=client,
    )

    assert result["data"]["function_signature"] == "inspectValues()"
    assert result["data"]["return_values"] == [
        ADDRESS,
        "0x1234",
        "7",
        True,
        "hello",
        [["1", "2"], ["3"]],
    ]


def test_contract_read_wraps_zero_and_one_outputs() -> None:
    client = _Web3()
    base = {
        "action": "contract_read",
        "contract_address": ADDRESS,
        "function_args": [],
    }
    client.eth.contract_result = None
    zero = inspect_blockchain(
        {
            **base,
            "function_abi": {
                "type": "function",
                "name": "touch",
                "inputs": [],
                "outputs": [],
                "stateMutability": "view",
            },
        },
        _context(),
        web3=client,
    )
    client.eth.contract_result = 9
    one = inspect_blockchain(
        {
            **base,
            "function_abi": {
                "type": "function",
                "name": "value",
                "inputs": [],
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
            },
        },
        _context(),
        web3=client,
    )

    assert zero["data"]["return_values"] == []
    assert one["data"]["return_values"] == ["9"]


def test_transaction_and_receipt_return_chain_facts() -> None:
    client = _Web3()

    transaction = inspect_blockchain(
        {"action": "transaction", "transaction_hash": TX_HASH},
        _context(),
        web3=client,
    )
    receipt = inspect_blockchain(
        {"action": "receipt", "transaction_hash": TX_HASH},
        _context(),
        web3=client,
    )

    assert transaction["data"]["value_wei"] == "5"
    assert transaction["data"]["input"] == "0x1234"
    assert transaction["data"]["block_number"] == "40"
    assert receipt["data"] == {
        "chain_id": 31337,
        "transaction_hash": TX_HASH,
        "state": "succeeded",
        "block_number": "41",
        "status": 1,
        "gas_used": "21000",
        "effective_gas_price_wei": "2",
    }


def test_missing_receipt_is_grounded_pending_success() -> None:
    client = _Web3()
    client.eth.receipt = None

    result = inspect_blockchain(
        {"action": "receipt", "transaction_hash": TX_HASH},
        _context(),
        web3=client,
    )

    assert result["ok"] is True
    assert result["data"]["state"] == "pending"
    assert result["data"]["status"] is None


def test_chain_mismatch_and_rpc_failure_are_typed() -> None:
    mismatch = inspect_blockchain(
        {"action": "chain_summary"}, _context(chain_id=1), web3=_Web3()
    )
    unavailable = inspect_blockchain(
        {"action": "chain_summary"}, _context(), web3=_FailingWeb3()
    )

    assert mismatch["error"] == {
        "code": "CHAIN_MISMATCH",
        "message": "Configured and observed chain IDs differ.",
        "retryable": False,
        "details": {"expected_chain_id": 1, "observed_chain_id": 31337},
    }
    assert unavailable["error"] == {
        "code": "RPC_UNAVAILABLE",
        "message": "Blockchain RPC operation failed.",
        "retryable": False,
        "details": {"operation": "chain_read"},
    }


def test_invalid_action_and_disabled_config_fail_without_rpc() -> None:
    invalid = inspect_blockchain(
        {"action": "eth_sendRawTransaction"}, _context(), web3=_Web3()
    )
    disabled_context = SimpleNamespace(policy=SimpleNamespace(raw={}))
    disabled = inspect_blockchain(
        {"action": "chain_summary"}, disabled_context, web3=_Web3()
    )

    assert invalid["error"]["code"] == "INVALID_ARGUMENT"
    assert disabled["error"]["code"] == "FEATURE_DISABLED"
