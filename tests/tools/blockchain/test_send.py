from types import SimpleNamespace
from typing import Any
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from eth_account import Account
from eth_abi.exceptions import DecodingError
import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

from openminion.modules.tool.plugin_api import PolicyAuthorization
from openminion.tools.blockchain.runtime import preparation_digest, send_transaction
from openminion.tools.blockchain.runtime import inspect_blockchain
from openminion.tools.blockchain.preparations import (
    MAX_OPERATION_RECORD_BYTES,
    SessionRecordError,
    save_resolved_preparation_record,
)
from openminion.tools.blockchain.resolution import ResolutionFailure
from openminion.tools.blockchain.transaction_schemas import (
    resolved_preparation_digest,
    validate_resolved_preparation_record,
)
from openminion.tools.blockchain.abi import encode_function_call
from openminion.tools.blockchain.schema_types import FunctionAbi

PRIVATE_KEY = "0x" + "11" * 32
SENDER = Account.from_key(PRIVATE_KEY).address
RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


def test_operation_result_bound_and_preterminal_reorg(monkeypatch) -> None:
    from openminion.tools.blockchain import resolved_operations

    operation = {
        "preparation_digest": "sha256:" + "1" * 64,
        "resolution_digest": "sha256:" + "2" * 64,
        "operation_digest": "sha256:" + "3" * 64,
        "transaction_hash": "0x" + "4" * 64,
        "submission_started": True,
        "broadcast_attempts": 1,
        "state": "confirming",
        "receipt_block_number": "10",
        "receipt_block_hash": "0x" + "5" * 64,
        "receipt_status": 1,
        "confirmations": 1,
        "confirmation_depth": 2,
        "gas_used": "21000",
        "effective_gas_price_wei": "1",
        "postcondition_results": [{"value": "x" * MAX_OPERATION_RECORD_BYTES}],
        "last_error_code": None,
    }
    assert resolved_operations._operation_result(operation)["error"]["code"] == (
        "RESULT_TOO_LARGE"
    )

    operation["postcondition_results"] = []
    monkeypatch.setattr(
        resolved_operations,
        "_persist_operation",
        lambda value, _expected, _context: dict(value),
    )
    result = resolved_operations._persist_missing_receipt(
        operation, dict(operation), object()
    )
    assert result["state"] == "reorged"


@pytest.mark.parametrize(
    ("reason", "code"),
    (("invalid", "OPERATION_INVALID"), ("size_limit", "RESULT_TOO_LARGE")),
)
def test_operation_status_classifies_invalid_storage(monkeypatch, reason, code) -> None:
    from openminion.tools.blockchain import resolved_operations

    def fail_load(*_args, **_kwargs):
        raise SessionRecordError("stored record rejected", reason=reason)

    monkeypatch.setattr(resolved_operations, "load_operation_record", fail_load)

    result = resolved_operations.operation_status(
        "sha256:" + "1" * 64,
        SimpleNamespace(session_id="session", env={}),
    )

    assert result["error"]["code"] == code


def test_operation_receipt_requires_matching_transaction_hash(monkeypatch) -> None:
    from openminion.tools.blockchain import resolved_operations

    monkeypatch.setattr(
        resolved_operations,
        "rpc_call",
        lambda *_args, **_kwargs: {
            "blockNumber": "0xa",
            "blockHash": "0x" + "5" * 64,
            "status": "0x1",
        },
    )

    _observation, error = resolved_operations._observe_operation_receipt(
        {"transaction_hash": "0x" + "4" * 64},
        {},
    )

    assert error["error"]["code"] == "OPERATION_INVALID"


def test_operation_receipt_requires_full_block_hash(monkeypatch) -> None:
    from openminion.tools.blockchain import resolved_operations

    transaction_hash = "0x" + "4" * 64
    monkeypatch.setattr(
        resolved_operations,
        "rpc_call",
        lambda *_args, **_kwargs: {
            "transactionHash": transaction_hash,
            "blockNumber": "0xa",
            "blockHash": "0x55",
            "status": "0x1",
        },
    )

    with pytest.raises(ResolutionFailure) as excinfo:
        resolved_operations._observe_operation_receipt(
            {"transaction_hash": transaction_hash},
            {},
        )

    assert excinfo.value.code == "RPC_UNAVAILABLE"


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


def test_configured_send_requires_the_optional_network_pair(tmp_path) -> None:
    context = _context(tmp_path)
    blockchain = context.policy.raw["context_metadata"]["runtime_tools"]["blockchain"]
    blockchain.pop("rpc_url")
    blockchain.pop("chain_id")
    client = _Web3()

    result = send_transaction({}, context, web3=client)

    assert result["error"] == {
        "code": "FEATURE_UNAVAILABLE",
        "message": "Configured blockchain network is unavailable.",
        "retryable": False,
        "details": {"feature": "configured_blockchain_network"},
    }
    assert client.eth.broadcasts == 0


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


def test_resolved_send_claims_once_and_status_survives_restart(
    tmp_path, monkeypatch
) -> None:
    from openminion.tools.blockchain import resolved_operations, resolved_calls

    context = _context(tmp_path)
    context.session_id = "resolved-session"
    context.env = {"OPENMINION_DATA_ROOT": str(tmp_path)}
    blockchain = context.policy.raw["context_metadata"]["runtime_tools"]["blockchain"]
    blockchain.pop("rpc_url")
    blockchain.pop("chain_id")
    blockchain["confirmation_depth"] = 2
    resolution_digest = "sha256:" + "12" * 32
    head_hashes = {
        10: "0x" + "10" * 32,
        11: "0x" + "11" * 32,
        42: "0x" + "42" * 32,
    }
    head = {"number": 42}
    setter = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "setApr",
            "inputs": [{"name": "value", "type": "uint256"}],
            "outputs": [],
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
    data = encode_function_call(Web3(), setter, [5])
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": SENDER,
        "to_address": RECIPIENT,
        "value_wei": "0",
        "nonce": "9",
        "gas_limit": "21000",
        "data": data,
        "max_fee_per_gas_wei": "23",
        "max_priority_fee_per_gas_wei": "3",
        "max_total_fee_wei": "483000",
    }
    prepared = {
        "schema_version": 2,
        "kind": "resolved_contract_call",
        "resolution_digest": resolution_digest,
        "resolved_context": {
            "resolution_digest": resolution_digest,
            "rpc_origin": "https://rpc.example",
            "sourcify_target_origin": "https://sourcify.dev",
            "sourcify_implementation_origin": None,
            "expected_chain_id": 31337,
            "observed_chain_id": 31337,
            "expected_genesis_hash": "0x" + "01" * 32,
            "observed_genesis_hash": "0x" + "01" * 32,
            "expected_checkpoint": None,
            "observed_checkpoint": None,
            "resolution_block_number": "40",
            "resolution_block_hash": "0x" + "40" * 32,
            "preparation_block_number": "42",
            "preparation_block_hash": head_hashes[42],
            "contract_address": RECIPIENT,
            "proxy_kind": "direct",
            "implementation_address": None,
            "abi_address": RECIPIENT,
            "target_code_hash": "0x" + "03" * 32,
            "implementation_code_hash": None,
            "function_abi": setter.model_dump(mode="json"),
        },
        "transaction": transaction,
        "call_context": {
            "function_abi": setter.model_dump(mode="json"),
            "function_args": [5],
            "function_signature": "setApr(uint256)",
        },
        "simulation": {
            "state": "succeeded",
            "chain_id": "31337",
            "block_identifier": "0x2a",
            "resolved_block_number": "42",
            "resolved_block_hash": head_hashes[42],
            "return_data": "0x",
            "gas_estimate": "21000",
            "decoded_returns": None,
        },
        "signer_address": SENDER,
        "postconditions": [
            {
                "function_signature": "apr()",
                "arguments": [],
                "expected_result": ["5"],
            }
        ],
        "preparation_digest": "sha256:" + "0" * 64,
    }
    prepared["preparation_digest"] = resolved_preparation_digest(prepared)
    save_resolved_preparation_record(
        prepared,
        context,
        validator=validate_resolved_preparation_record,
        digester=resolved_preparation_digest,
    )
    resolved_context = prepared["resolved_context"]
    record = {
        "resolution_digest": resolution_digest,
        "rpc_url": "https://rpc.example/",
        "sourcify_target_url": "https://sourcify.dev/target",
        "sourcify_target_match": "exact_match",
        "sourcify_implementation_url": None,
        "sourcify_implementation_match": None,
        "expected_chain_id": resolved_context["expected_chain_id"],
        "observed_chain_id": resolved_context["observed_chain_id"],
        "expected_genesis_hash": resolved_context["expected_genesis_hash"],
        "observed_genesis_hash": resolved_context["observed_genesis_hash"],
        "expected_checkpoint": None,
        "observed_checkpoint": None,
        "verification_block_number": resolved_context["resolution_block_number"],
        "verification_block_hash": resolved_context["resolution_block_hash"],
        "contract_address": RECIPIENT,
        "proxy_kind": "direct",
        "implementation_address": None,
        "abi_address": RECIPIENT,
        "target_code_hash": resolved_context["target_code_hash"],
        "implementation_code_hash": None,
    }
    receipt = {"present": True}
    rpc_state = {
        "balance": "0xde0b6b3a7640000",
        "priority_fee": "0x3",
        "postcondition_error": False,
        "transaction_hash": None,
    }
    broadcast_count = 0
    broadcast_lock = Lock()

    monkeypatch.setattr(
        resolved_operations, "load_resolution", lambda digest, ctx: record
    )
    monkeypatch.setattr(
        resolved_operations,
        "revalidate_resolution",
        lambda value: {
            "block_number": str(head["number"]),
            "block_hash": head_hashes[head["number"]],
        },
    )
    monkeypatch.setattr(
        resolved_operations,
        "revalidate_chain_identity",
        lambda value: {
            "block_number": str(head["number"]),
            "block_hash": head_hashes[head["number"]],
        },
    )
    monkeypatch.setattr(
        resolved_operations,
        "function_by_signature",
        lambda value, signature: setter if signature.startswith("setApr") else reader,
    )

    def rpc_call(_record, method, params):
        nonlocal broadcast_count
        if method == "eth_getBalance":
            assert params[1] == "latest"
            return rpc_state["balance"]
        if method == "eth_getTransactionCount":
            return "0x9"
        if method == "eth_maxPriorityFeePerGas":
            return rpc_state["priority_fee"]
        if method == "eth_call":
            if params[1] == "0xa":
                if rpc_state["postcondition_error"]:
                    raise ResolutionFailure(
                        "RPC_UNAVAILABLE", "Postcondition observation failed."
                    )
                return "0x" + Web3().codec.encode(["uint256"], [5]).hex()
            return "0x"
        if method == "eth_sendRawTransaction":
            with broadcast_lock:
                broadcast_count += 1
            rpc_state["transaction_hash"] = "0x" + Web3.keccak(hexstr=params[0]).hex()
            return rpc_state["transaction_hash"]
        if method == "eth_getTransactionReceipt":
            if not receipt["present"]:
                return None
            return {
                "transactionHash": rpc_state["transaction_hash"],
                "blockNumber": "0xa",
                "blockHash": head_hashes[10],
                "status": "0x1",
                "gasUsed": "0x5208",
                "effectiveGasPrice": "0x17",
            }
        assert method == "eth_getBlockByNumber"
        number = int(params[0], 16)
        return {
            "number": hex(number),
            "hash": head_hashes[number],
            "baseFeePerGas": "0xa",
        }

    monkeypatch.setattr(resolved_operations, "rpc_call", rpc_call)
    monkeypatch.setattr(resolved_calls, "rpc_call", rpc_call)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sends = list(
            pool.map(lambda _index: send_transaction(prepared, context), range(2))
        )

    assert broadcast_count == 1
    assert sorted(result["state"] for result in sends) == ["failed", "pending"]
    assert any(
        (result.get("error") or {}).get("code") == "OPERATION_INVALID"
        for result in sends
    )

    def reject_current_lineage(_value):
        raise ResolutionFailure("STALE_RESOLUTION", "Proxy lineage changed.")

    monkeypatch.setattr(
        resolved_operations,
        "revalidate_resolution",
        reject_current_lineage,
    )

    head["number"] = 10
    confirming = inspect_blockchain(
        {
            "action": "operation_status",
            "preparation_digest": prepared["preparation_digest"],
        },
        context,
    )
    assert confirming["state"] == "succeeded"
    assert confirming["data"]["state"] == "confirming"
    assert confirming["data"]["confirmations"] == 1

    restarted_context = _context(tmp_path)
    restarted_context.session_id = context.session_id
    restarted_context.env = context.env
    restarted_blockchain = restarted_context.policy.raw["context_metadata"][
        "runtime_tools"
    ]["blockchain"]
    restarted_blockchain.pop("rpc_url")
    restarted_blockchain.pop("chain_id")
    head["number"] = 11
    succeeded = inspect_blockchain(
        {
            "action": "operation_status",
            "preparation_digest": prepared["preparation_digest"],
        },
        restarted_context,
    )
    assert succeeded["state"] == "succeeded"
    assert succeeded["data"]["state"] == "succeeded"
    assert succeeded["data"]["postcondition_results"][0]["matched"] is True

    original_decode = resolved_operations.decode_abi_values

    def fail_decode(*_args, **_kwargs):
        raise DecodingError("invalid return data")

    monkeypatch.setattr(resolved_operations, "decode_abi_values", fail_decode)
    decode_failed = inspect_blockchain(
        {
            "action": "operation_status",
            "preparation_digest": prepared["preparation_digest"],
        },
        restarted_context,
    )
    assert decode_failed == succeeded
    monkeypatch.setattr(resolved_operations, "decode_abi_values", original_decode)

    rpc_state["postcondition_error"] = True
    observation_failed = inspect_blockchain(
        {
            "action": "operation_status",
            "preparation_digest": prepared["preparation_digest"],
        },
        restarted_context,
    )
    assert observation_failed == succeeded
    rpc_state["postcondition_error"] = False

    receipt["present"] = False
    reorged = inspect_blockchain(
        {
            "action": "operation_status",
            "preparation_digest": prepared["preparation_digest"],
        },
        restarted_context,
    )
    assert reorged == succeeded

    monkeypatch.setattr(
        resolved_operations,
        "revalidate_resolution",
        lambda value: {
            "block_number": str(head["number"]),
            "block_hash": head_hashes[head["number"]],
        },
    )

    repeated = send_transaction(prepared, restarted_context)
    assert repeated["error"]["code"] == "OPERATION_INVALID"
    assert broadcast_count == 1

    context.session_id = "insufficient-session"
    save_resolved_preparation_record(
        prepared,
        context,
        validator=validate_resolved_preparation_record,
        digester=resolved_preparation_digest,
    )
    rpc_state["balance"] = "0x1"
    insufficient = send_transaction(prepared, context)
    assert insufficient["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert broadcast_count == 1

    context.session_id = "stale-fee-session"
    save_resolved_preparation_record(
        prepared,
        context,
        validator=validate_resolved_preparation_record,
        digester=resolved_preparation_digest,
    )
    rpc_state["balance"] = "0xde0b6b3a7640000"
    rpc_state["priority_fee"] = "0x4"
    stale_fee = send_transaction(prepared, context)
    assert stale_fee["error"]["code"] == "STALE_PREPARATION"
    assert stale_fee["error"]["details"] == {"fields": ["fee"]}
    assert broadcast_count == 1
    rpc_state["priority_fee"] = "0x3"

    context.session_id = "claim-failure-session"
    save_resolved_preparation_record(
        prepared,
        context,
        validator=validate_resolved_preparation_record,
        digester=resolved_preparation_digest,
    )
    original_claim = resolved_operations.claim_operation_record

    def fail_claim(*_args, **_kwargs):
        raise SessionRecordError("unavailable", reason="unavailable")

    monkeypatch.setattr(resolved_operations, "claim_operation_record", fail_claim)
    claim_failed = send_transaction(prepared, context)
    assert claim_failed["error"]["code"] == "OPERATION_UNAVAILABLE"
    assert claim_failed["data"]["broadcast_attempts"] == 0
    assert broadcast_count == 1
    monkeypatch.setattr(resolved_operations, "claim_operation_record", original_claim)

    context.session_id = "replace-failure-session"
    save_resolved_preparation_record(
        prepared,
        context,
        validator=validate_resolved_preparation_record,
        digester=resolved_preparation_digest,
    )

    def fail_replace(*_args, **_kwargs):
        raise SessionRecordError("unavailable", reason="unavailable")

    monkeypatch.setattr(resolved_operations, "replace_operation_record", fail_replace)
    replace_failed = send_transaction(prepared, context)
    assert replace_failed["state"] == "broadcast_unknown"
    assert replace_failed["error"]["code"] == "OPERATION_UNAVAILABLE"
    assert replace_failed["data"]["broadcast_attempts"] == 1
    assert broadcast_count == 2
