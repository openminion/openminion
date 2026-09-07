from types import SimpleNamespace
from typing import Any

from eth_account import Account
import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

from openminion.modules.tool.plugin_api import PolicyAuthorization
from openminion.tools.blockchain.runtime import preparation_digest, send_transaction

PRIVATE_KEY = "0x" + "11" * 32
SENDER = Account.from_key(PRIVATE_KEY).address
RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


class _SecretService:
    def get_secret_sync(self, key: str, *, namespace: str) -> str:
        assert key == "signer"
        assert namespace == "blockchain"
        return PRIVATE_KEY


class _Eth:
    account = Account
    chain_id = 31337

    def __init__(self) -> None:
        self.broadcasts = 0
        self.nonce = 9
        self.hash = "0x" + "ab" * 32
        self.broadcast_error: Exception | None = None
        self.receipt_error: Exception | None = None
        self.call_error: Exception | None = None
        self.receipt_status = 1

    def get_transaction_count(self, address: str, block: str) -> int:
        assert address == SENDER
        assert block == "pending"
        return self.nonce

    def call(self, transaction: dict[str, Any], block: str) -> bytes:
        assert transaction["from"] == SENDER
        assert block == "pending"
        if self.call_error is not None:
            raise self.call_error
        return b""

    def send_raw_transaction(self, raw: bytes) -> str:
        assert raw
        self.broadcasts += 1
        if self.broadcast_error is not None:
            raise self.broadcast_error
        return self.hash

    def wait_for_transaction_receipt(self, tx_hash: str, *, timeout: int):
        assert tx_hash == self.hash
        assert timeout == 60
        if self.receipt_error is not None:
            raise self.receipt_error
        return {
            "status": self.receipt_status,
            "blockNumber": 10,
            "gasUsed": 21000,
            "effectiveGasPrice": 23,
        }

    def get_transaction_receipt(self, tx_hash: str):
        return self.wait_for_transaction_receipt(tx_hash, timeout=60)


class _Web3:
    def __init__(self) -> None:
        self.eth = _Eth()

    @staticmethod
    def to_checksum_address(value: str) -> str:
        return Web3.to_checksum_address(value)

    @staticmethod
    def keccak(value: bytes) -> bytes:
        return Web3.keccak(value)


def _transaction() -> dict[str, Any]:
    return {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": SENDER,
        "to_address": RECIPIENT,
        "value_wei": "1",
        "nonce": "9",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "23",
        "max_priority_fee_per_gas_wei": "3",
        "max_total_fee_wei": "483000",
    }


def _context(tmp_path, *, authorized: bool = True):
    events: list[dict[str, Any]] = []
    transaction = _transaction()
    authorization = (
        PolicyAuthorization(
            tool="blockchain",
            method="send_transaction",
            invocation_hash="hash",
            approval_id="approval",
            grant_id="grant",
            duration_type="once",
        )
        if authorized
        else None
    )
    return SimpleNamespace(
        secret_service=_SecretService(),
        policy_authorization=authorization,
        invocation_id="invocation",
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "runtime_tools": {
                        "blockchain": {
                            "enabled": True,
                            "rpc_url": "http://127.0.0.1:8545",
                            "chain_id": 31337,
                            "signer_secret_key": "signer",
                            "signer_secret_namespace": "blockchain",
                            "writes_enabled": True,
                            "max_total_fee_wei": "10000000000000000",
                            "receipt_timeout_seconds": 60,
                        }
                    }
                }
            }
        ),
        write_audit_event=lambda event: events.append(event) is None,
        events=events,
        transaction=transaction,
    )


def test_send_requires_trusted_authorization_before_signing(tmp_path) -> None:
    context = _context(tmp_path, authorized=False)
    client = _Web3()
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["error"]["code"] == "POLICY_MODE_UNSUPPORTED"
    assert client.eth.broadcasts == 0
    assert context.events == []


def test_send_broadcasts_once_and_records_terminal_audit(tmp_path) -> None:
    context = _context(tmp_path)
    client = _Web3()
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["ok"] is True
    assert result["state"] == "succeeded"
    assert set(result) == {"ok", "state", "data", "error"}
    assert result["error"] is None
    assert result["data"]["audit_recorded"] is True
    assert result["data"]["receipt_status"] == 1
    assert client.eth.broadcasts == 1
    assert context.events[0]["approval_id"] == "approval"
    assert context.events[0]["consumed_grant_id"] == "grant"
    assert context.events[0]["broadcast_attempts"] == 1


def test_send_rejects_stale_nonce_without_broadcast(tmp_path) -> None:
    context = _context(tmp_path)
    client = _Web3()
    client.eth.nonce = 10
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["error"]["code"] == "STALE_PREPARATION"
    assert result["error"]["details"]["fields"] == ["nonce"]
    assert result["data"]["transaction_hash"] == ""
    assert result["data"]["broadcast_attempts"] == 0
    assert client.eth.broadcasts == 0
    assert context.events[0]["state"] == "stale"


def test_send_time_contract_revert_is_structured_without_broadcast(
    tmp_path,
) -> None:
    context = _context(tmp_path)
    client = _Web3()
    client.eth.call_error = ContractLogicError("execution reverted")
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["error"]["code"] == "SIMULATION_REVERTED"
    assert result["error"]["details"] == {
        "stage": "send",
        "revert": {"kind": "data_unavailable", "raw_data": None},
        "broadcast_attempted": False,
    }
    assert client.eth.broadcasts == 0
    assert context.events[0]["broadcast_attempts"] == 0


def test_send_returns_broadcast_unknown_without_retry(tmp_path) -> None:
    context = _context(tmp_path)
    client = _Web3()
    client.eth.broadcast_error = OSError("connection dropped")
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["state"] == "broadcast_unknown"
    assert result["error"]["code"] == "BROADCAST_UNKNOWN"
    assert result["error"]["retryable"] is False
    assert result["data"]["transaction_hash"].startswith("0x")
    assert result["data"]["broadcast_attempts"] == 1
    assert client.eth.broadcasts == 1


@pytest.mark.parametrize(
    ("receipt_error", "error_code"),
    [
        (TimeExhausted(), "RECEIPT_PENDING"),
        (OSError("receipt unavailable"), "RPC_UNAVAILABLE"),
    ],
)
def test_send_returns_pending_without_rebroadcast(
    tmp_path,
    receipt_error: Exception,
    error_code: str,
) -> None:
    context = _context(tmp_path)
    client = _Web3()
    client.eth.receipt_error = receipt_error
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["state"] == "pending"
    assert result["error"]["code"] == error_code
    assert result["error"]["retryable"] is False
    assert result["data"]["accepted"] is True
    assert result["data"]["broadcast_attempts"] == 1
    assert client.eth.broadcasts == 1


def test_send_returns_mined_revert_without_rebroadcast(tmp_path) -> None:
    context = _context(tmp_path)
    client = _Web3()
    client.eth.receipt_status = 0
    transaction = context.transaction

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=client,
    )

    assert result["state"] == "reverted"
    assert result["error"]["code"] == "TRANSACTION_REVERTED"
    assert result["data"]["transaction_hash"] == client.eth.hash
    assert result["data"]["broadcast_attempts"] == 1
    assert result["data"]["receipt_status"] == 0
    assert result["data"]["gas_used"] == "21000"
    assert client.eth.broadcasts == 1
