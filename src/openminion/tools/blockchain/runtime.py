from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import re
from typing import Any, cast

from pydantic import ValidationError

from .config import resolve_blockchain_config
from .schemas import (
    INSPECT_REQUEST_ADAPTER,
    PREPARE_REQUEST_ADAPTER,
    SEND_REQUEST_ADAPTER,
    CallContext,
    FunctionAbi,
    PreparedTransactionResult,
)


class _RpcFailure(RuntimeError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(operation)


class _ChainMismatch(RuntimeError):
    def __init__(self, observed_chain_id: int) -> None:
        self.observed_chain_id = observed_chain_id
        super().__init__(str(observed_chain_id))


def _error(code: str, message: str, details: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "state": "failed",
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "details": dict(details),
        },
    }


def _rpc(operation: str, call: Callable[[], Any]) -> Any:
    from web3.exceptions import Web3Exception

    try:
        return call()
    except (OSError, ValueError, Web3Exception) as exc:
        raise _RpcFailure(operation) from exc


def _client(rpc_url: str) -> Any:
    from web3 import HTTPProvider, Web3

    return Web3(HTTPProvider(rpc_url))


def _decimal(value: Any) -> str:
    return str(int(value))


def _hex_data(value: Any) -> str:
    if isinstance(value, str):
        token = value
    elif hasattr(value, "hex"):
        token = value.hex()
    else:
        token = bytes(value).hex()
    return token if token.startswith("0x") else f"0x{token}"


_ARRAY_TYPE_RE = re.compile(r"^(.*)\[(?:[0-9]*)\]$")


def _normalize_abi_value(value: Any, abi_type: str, web3: Any) -> Any:
    array_match = _ARRAY_TYPE_RE.fullmatch(abi_type)
    if array_match is not None:
        item_type = array_match.group(1)
        return [_normalize_abi_value(item, item_type, web3) for item in value]
    if abi_type == "address":
        return web3.to_checksum_address(value)
    if abi_type == "bytes" or abi_type.startswith("bytes"):
        return _hex_data(value)
    if abi_type.startswith("uint") or abi_type.startswith("int"):
        return _decimal(value)
    if abi_type in {"bool", "string"}:
        return value
    raise ValueError(f"unsupported ABI output type: {abi_type}")


def _normalize_contract_outputs(
    value: Any,
    outputs: Sequence[Any],
    web3: Any,
) -> list[Any]:
    if not outputs:
        return []
    if len(outputs) == 1:
        values = [value]
    else:
        values = list(value)
    return [
        _normalize_abi_value(item, output.type, web3)
        for item, output in zip(values, outputs, strict=True)
    ]


def _function_signature(function_abi: FunctionAbi) -> str:
    input_types = ",".join(parameter.type for parameter in function_abi.inputs)
    return f"{function_abi.name}({input_types})"


def preparation_digest(
    transaction: Mapping[str, Any],
    call_context: Mapping[str, Any] | None,
) -> str:
    encoded = json.dumps(
        {"transaction": dict(transaction), "call_context": call_context},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _chain_id(web3: Any, expected_chain_id: int) -> int:
    observed = int(_rpc("chain_read", lambda: web3.eth.chain_id))
    if observed != expected_chain_id:
        raise _ChainMismatch(observed)
    return observed


def _inspect_data(request: Any, client: Any, chain_id: int) -> dict[str, Any]:
    if request.action == "chain_summary":
        return {
            "chain_id": chain_id,
            "latest_block_number": _decimal(
                _rpc("chain_read", lambda: client.eth.block_number)
            ),
        }
    if request.action == "native_balance":
        address = client.to_checksum_address(request.address)
        return {
            "chain_id": chain_id,
            "address": address,
            "balance_wei": _decimal(
                _rpc("chain_read", lambda: client.eth.get_balance(address))
            ),
        }
    if request.action == "bytecode":
        address = client.to_checksum_address(request.address)
        bytecode = _hex_data(_rpc("chain_read", lambda: client.eth.get_code(address)))
        return {
            "chain_id": chain_id,
            "address": address,
            "has_code": bytecode != "0x",
            "bytecode": bytecode,
        }
    if request.action == "contract_read":
        address = client.to_checksum_address(request.contract_address)
        abi = request.function_abi.model_dump(mode="json")
        function = client.eth.contract(address=address, abi=[abi]).get_function_by_name(
            request.function_abi.name
        )(*request.function_args)
        raw = _rpc("chain_read", lambda: function.call(block_identifier="pending"))
        return {
            "chain_id": chain_id,
            "contract_address": address,
            "function_signature": _function_signature(request.function_abi),
            "return_values": _normalize_contract_outputs(
                raw, request.function_abi.outputs, client
            ),
        }
    if request.action == "transaction":
        transaction = _rpc(
            "chain_read",
            lambda: client.eth.get_transaction(request.transaction_hash),
        )
        to_address = transaction.get("to")
        return {
            "chain_id": chain_id,
            "transaction_hash": request.transaction_hash,
            "from_address": client.to_checksum_address(transaction["from"]),
            "to_address": client.to_checksum_address(to_address) if to_address else None,
            "value_wei": _decimal(transaction["value"]),
            "input": _hex_data(transaction["input"]),
            "nonce": _decimal(transaction["nonce"]),
            "block_number": (
                _decimal(transaction["blockNumber"])
                if transaction.get("blockNumber") is not None
                else None
            ),
        }
    return _receipt_data(client, chain_id, request.transaction_hash)


def inspect_blockchain(
    args: Mapping[str, Any],
    context: Any | None,
    *,
    web3: Any | None = None,
) -> dict[str, Any]:
    config = resolve_blockchain_config(context)
    if not config.enabled:
        return _error(
            "FEATURE_DISABLED",
            "Blockchain capability is disabled.",
            {"feature": "blockchain"},
        )
    try:
        request = INSPECT_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "request_schema"},
        )

    client = web3 or _client(config.rpc_url)
    try:
        chain_id = _chain_id(client, cast(int, config.chain_id))
    except _RpcFailure as exc:
        return _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": exc.operation},
        )
    except _ChainMismatch as exc:
        return _error(
            "CHAIN_MISMATCH",
            "Configured and observed chain IDs differ.",
            {
                "expected_chain_id": config.chain_id,
                "observed_chain_id": exc.observed_chain_id,
            },
        )

    try:
        data = _inspect_data(request, client, chain_id)
    except _RpcFailure as exc:
        return _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": exc.operation},
        )

    return {
        "ok": True,
        "state": "succeeded",
        "action": request.action,
        "data": data,
    }


def _build_prepared_transaction(
    request: Any,
    client: Any,
    account: Any,
    expected_chain_id: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any], int, int]:
    chain_id = _chain_id(client, expected_chain_id)
    sender = client.to_checksum_address(account.address)
    if request.kind == "contract_call":
        recipient = client.to_checksum_address(request.contract_address)
        abi = request.function_abi.model_dump(mode="json")
        data = client.eth.contract(abi=[abi]).encode_abi(
            request.function_abi.name,
            args=request.function_args,
        )
        call_context = CallContext(
            function_abi=request.function_abi,
            function_args=request.function_args,
            function_signature=_function_signature(request.function_abi),
        ).model_dump(mode="json")
    else:
        recipient = client.to_checksum_address(request.to_address)
        data = "0x" if request.kind == "native_transfer" else request.data
        call_context = None
    value = int(request.value_wei)
    nonce = int(
        _rpc("chain_read", lambda: client.eth.get_transaction_count(sender, "pending"))
    )
    rpc_transaction = {
        "chainId": chain_id,
        "from": sender,
        "to": recipient,
        "value": value,
        "nonce": nonce,
        "data": data,
    }
    gas_limit = int(_rpc("estimate_gas", lambda: client.eth.estimate_gas(rpc_transaction)))
    pending_block = _rpc("chain_read", lambda: client.eth.get_block("pending"))
    base_fee = pending_block.get("baseFeePerGas")
    common = {
        "schema_version": "evm-transaction-v1",
        "chain_id": chain_id,
        "from_address": sender,
        "to_address": recipient,
        "value_wei": str(value),
        "nonce": str(nonce),
        "gas_limit": str(gas_limit),
        "data": data,
    }
    if base_fee is None:
        gas_price = int(_rpc("chain_read", lambda: client.eth.gas_price))
        max_total_fee = gas_limit * gas_price
        normalized = {
            **common,
            "transaction_type": "legacy",
            "gas_price_wei": str(gas_price),
            "max_total_fee_wei": str(max_total_fee),
        }
    else:
        priority_fee = int(_rpc("chain_read", lambda: client.eth.max_priority_fee))
        max_fee = 2 * int(base_fee) + priority_fee
        max_total_fee = gas_limit * max_fee
        normalized = {
            **common,
            "transaction_type": "eip1559",
            "max_fee_per_gas_wei": str(max_fee),
            "max_priority_fee_per_gas_wei": str(priority_fee),
            "max_total_fee_wei": str(max_total_fee),
        }
    return normalized, call_context, rpc_transaction, gas_limit, max_total_fee


def prepare_transaction(
    args: Mapping[str, Any],
    context: Any | None,
    *,
    web3: Any | None = None,
) -> dict[str, Any]:
    from web3.exceptions import Web3Exception

    config = resolve_blockchain_config(context)
    if not config.enabled:
        return _error(
            "FEATURE_DISABLED",
            "Blockchain capability is disabled.",
            {"feature": "blockchain"},
        )
    try:
        request = PREPARE_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "request_schema"},
        )

    secret_service = getattr(context, "secret_service", None)
    if secret_service is None or not config.signer_secret_key:
        return _signer_unavailable()
    try:
        private_key = secret_service.get_secret_sync(
            config.signer_secret_key,
            namespace=config.signer_secret_namespace,
        )
        client = web3 or _client(config.rpc_url)
        account = client.eth.account.from_key(private_key)
    except (KeyError, OSError, TypeError, ValueError):
        return _signer_unavailable()

    try:
        normalized, call_context, rpc_transaction, gas_limit, max_total_fee = (
            _build_prepared_transaction(
                request,
                client,
                account,
                cast(int, config.chain_id),
            )
        )
    except _RpcFailure as exc:
        return _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": exc.operation},
        )
    except _ChainMismatch as exc:
        return _error(
            "CHAIN_MISMATCH",
            "Configured and observed chain IDs differ.",
            {
                "expected_chain_id": config.chain_id,
                "observed_chain_id": exc.observed_chain_id,
            },
        )
    except ValueError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "transaction_encoding"},
        )

    configured_cap = int(config.max_total_fee_wei)
    if max_total_fee > configured_cap:
        return _error(
            "FEE_CAP_EXCEEDED",
            "Transaction fee exceeds the configured cap.",
            {
                "max_total_fee_wei": str(max_total_fee),
                "configured_max_total_fee_wei": str(configured_cap),
            },
        )
    try:
        client.eth.call({**rpc_transaction, "gas": gas_limit}, "pending")
    except (OSError, ValueError, Web3Exception):
        return _error(
            "SIMULATION_REVERTED",
            "Transaction simulation reverted.",
            {"stage": "prepare"},
        )

    digest = preparation_digest(normalized, call_context)
    return PreparedTransactionResult.model_validate(
        {
            "ok": True,
            "state": "prepared",
            "transaction": normalized,
            "call_context": call_context,
            "simulation": {"state": "succeeded"},
            "preparation_digest": digest,
        }
    ).model_dump(mode="json")


def _signer_unavailable() -> dict[str, Any]:
    return _error(
        "SIGNER_UNAVAILABLE",
        "Configured blockchain signer is unavailable.",
        {},
    )


def _rpc_transaction(transaction: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "chainId": int(transaction["chain_id"]),
        "from": transaction["from_address"],
        "to": transaction["to_address"],
        "value": int(transaction["value_wei"]),
        "nonce": int(transaction["nonce"]),
        "gas": int(transaction["gas_limit"]),
        "data": transaction["data"],
    }
    if transaction["transaction_type"] == "eip1559":
        payload["maxFeePerGas"] = int(transaction["max_fee_per_gas_wei"])
        payload["maxPriorityFeePerGas"] = int(
            transaction["max_priority_fee_per_gas_wei"]
        )
    else:
        payload["gasPrice"] = int(transaction["gas_price_wei"])
    return payload


def _load_signing_account(config: Any, context: Any, web3: Any | None) -> tuple[Any, Any]:
    secret_service = getattr(context, "secret_service", None)
    if secret_service is None or not config.signer_secret_key:
        raise KeyError("signer unavailable")
    private_key = secret_service.get_secret_sync(
        config.signer_secret_key,
        namespace=config.signer_secret_namespace,
    )
    client = web3 or _client(config.rpc_url)
    return client, client.eth.account.from_key(private_key)


def _digest_mismatch_terminal(
    context: Any,
    request: Any,
    transaction: Mapping[str, Any],
    call_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if preparation_digest(transaction, call_context) == request.preparation_digest:
        return None
    return _send_terminal(
        context,
        transaction,
        request.preparation_digest,
        "stale",
        _error(
            "STALE_PREPARATION",
            "Prepared transaction no longer matches chain state.",
            {"fields": ["data"]},
        ),
    )


def _signer_mismatch_terminal(
    context: Any,
    transaction: Mapping[str, Any],
    digest: str,
    client: Any,
    sender: str,
) -> dict[str, Any] | None:
    expected_sender = client.to_checksum_address(transaction["from_address"])
    if sender == expected_sender:
        return None
    return _send_terminal(
        context,
        transaction,
        digest,
        "failed",
        _error(
            "SIGNER_MISMATCH",
            "Prepared sender does not match the configured signer.",
            {
                "expected_from_address": expected_sender,
                "observed_from_address": sender,
            },
        ),
    )


def _validate_send_state(
    client: Any,
    config: Any,
    transaction: Mapping[str, Any],
    call_context: Any | None,
    sender: str,
) -> tuple[dict[str, Any], list[str]]:
    stale_fields: list[str] = []
    try:
        if _chain_id(client, int(config.chain_id)) != int(transaction["chain_id"]):
            stale_fields.append("chain_id")
        if int(client.eth.get_transaction_count(sender, "pending")) != int(
            transaction["nonce"]
        ):
            stale_fields.append("nonce")
        rpc_transaction = _rpc_transaction(transaction)
        if call_context is not None:
            abi = call_context.function_abi.model_dump(mode="json")
            encoded = client.eth.contract(abi=[abi]).encode_abi(
                call_context.function_abi.name,
                args=call_context.function_args,
            )
            if encoded != transaction["data"]:
                stale_fields.append("data")
        if int(transaction["max_total_fee_wei"]) > int(config.max_total_fee_wei):
            stale_fields.append("fees")
        client.eth.call(rpc_transaction, "pending")
    except _ChainMismatch:
        stale_fields.append("chain_id")
        rpc_transaction = _rpc_transaction(transaction)
    except ValueError:
        stale_fields.append("simulation")
        rpc_transaction = _rpc_transaction(transaction)
    return rpc_transaction, stale_fields


def _receipt_terminal(
    context: Any,
    config: Any,
    request: Any,
    transaction: Mapping[str, Any],
    transaction_hash: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    receipt_data = _receipt_from_mapping(
        int(config.chain_id), transaction_hash, receipt
    )
    if int(receipt["status"]) == 0:
        result = {
            **_error(
                "TRANSACTION_REVERTED",
                "Transaction was mined and reverted.",
                {
                    "transaction_hash": transaction_hash,
                    "block_number": str(receipt["blockNumber"]),
                    "receipt_status": 0,
                },
            ),
            "receipt": receipt_data,
        }
        state = "reverted"
    else:
        result = {
            "ok": True,
            "state": "succeeded",
            "transaction_hash": transaction_hash,
            "receipt": receipt_data,
            "preparation_digest": request.preparation_digest,
        }
        state = "succeeded"
    return _send_terminal(
        context,
        transaction,
        request.preparation_digest,
        state,
        result,
        transaction_hash=transaction_hash,
        broadcast_attempts=1,
    )


def _submit_transaction(
    context: Any,
    config: Any,
    request: Any,
    transaction: Mapping[str, Any],
    client: Any,
    account: Any,
    rpc_transaction: Mapping[str, Any],
) -> dict[str, Any]:
    from web3.exceptions import TimeExhausted, Web3Exception

    signed = account.sign_transaction(
        {key: value for key, value in rpc_transaction.items() if key != "from"}
    )
    raw_transaction = signed.raw_transaction
    transaction_hash = _hex_data(client.keccak(raw_transaction))
    try:
        submitted_hash = _hex_data(client.eth.send_raw_transaction(raw_transaction))
    except (OSError, ValueError, Web3Exception):
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "broadcast_unknown",
            _error(
                "BROADCAST_UNKNOWN",
                "Transaction submission outcome is unknown.",
                {"transaction_hash": transaction_hash, "broadcast_attempts": 1},
            ),
            transaction_hash=transaction_hash,
            broadcast_attempts=1,
        )
    try:
        receipt = client.eth.wait_for_transaction_receipt(
            submitted_hash,
            timeout=config.receipt_timeout_seconds,
        )
    except TimeExhausted:
        result = _error(
            "RECEIPT_PENDING",
            "Transaction was accepted but is not yet mined.",
            {"transaction_hash": submitted_hash, "accepted": True},
        )
        state = "pending"
    except (OSError, ValueError, Web3Exception):
        result = _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": "receipt"},
        )
        state = "pending"
    else:
        return _receipt_terminal(
            context, config, request, transaction, submitted_hash, receipt
        )
    return _send_terminal(
        context,
        transaction,
        request.preparation_digest,
        state,
        result,
        transaction_hash=submitted_hash,
        broadcast_attempts=1,
    )


def send_transaction(
    args: Mapping[str, Any],
    context: Any,
    *,
    web3: Any | None = None,
) -> dict[str, Any]:
    from web3.exceptions import Web3Exception

    config = resolve_blockchain_config(context)
    if not config.enabled or not config.writes_enabled:
        return _error(
            "FEATURE_DISABLED",
            "Blockchain capability is disabled.",
            {"feature": "blockchain_writes"},
        )
    try:
        request = SEND_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "request_schema"},
        )
    authorization = getattr(context, "policy_authorization", None)
    if authorization is None:
        return _error(
            "POLICY_MODE_UNSUPPORTED",
            "Enforcing policy is required for blockchain send.",
            {"mode": "unavailable"},
        )
    transaction = request.transaction.model_dump(mode="json")
    call_context = (
        request.call_context.model_dump(mode="json")
        if request.call_context is not None
        else None
    )
    digest_error = _digest_mismatch_terminal(
        context, request, transaction, call_context
    )
    if digest_error is not None:
        return digest_error
    try:
        client, account = _load_signing_account(config, context, web3)
    except (KeyError, OSError, TypeError, ValueError):
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "failed",
            _signer_unavailable(),
        )
    sender = client.to_checksum_address(account.address)
    signer_error = _signer_mismatch_terminal(
        context, transaction, request.preparation_digest, client, sender
    )
    if signer_error is not None:
        return signer_error
    try:
        rpc_transaction, stale_fields = _validate_send_state(
            client,
            config,
            transaction,
            request.call_context,
            sender,
        )
    except (OSError, Web3Exception):
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "failed",
            _error(
                "RPC_UNAVAILABLE",
                "Blockchain RPC operation failed.",
                {"operation": "simulate"},
            ),
        )
    if stale_fields:
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "stale",
            _error(
                "STALE_PREPARATION",
                "Prepared transaction no longer matches chain state.",
                {"fields": sorted(set(stale_fields))},
            ),
        )

    return _submit_transaction(
        context,
        config,
        request,
        transaction,
        client,
        account,
        rpc_transaction,
    )


def _send_terminal(
    context: Any,
    transaction: Mapping[str, Any],
    digest: str,
    state: str,
    result: dict[str, Any],
    *,
    transaction_hash: str = "",
    broadcast_attempts: int = 0,
) -> dict[str, Any]:
    authorization = context.policy_authorization
    audit_recorded = context.write_audit_event(
        {
            "event_type": "tool.blockchain.transaction",
            "tool_name": "blockchain.send_transaction",
            "invocation_id": str(getattr(context, "invocation_id", "") or ""),
            "invocation_hash": authorization.invocation_hash,
            "approval_id": authorization.approval_id,
            "consumed_grant_id": authorization.grant_id,
            "duration_type": authorization.duration_type,
            "chain_id": int(transaction["chain_id"]),
            "from_address": transaction["from_address"],
            "to_address": transaction["to_address"],
            "value_wei": transaction["value_wei"],
            "preparation_digest": digest,
            "transaction_hash": transaction_hash,
            "state": state,
            "broadcast_attempts": broadcast_attempts,
        }
    )
    data = {
        "chain_id": int(transaction["chain_id"]),
        "from_address": transaction["from_address"],
        "to_address": transaction["to_address"],
        "value_wei": transaction["value_wei"],
        "preparation_digest": digest,
        "transaction_hash": transaction_hash,
        "broadcast_attempts": broadcast_attempts,
        "audit_recorded": audit_recorded,
    }
    if state == "pending":
        data["accepted"] = True
    receipt = result.get("receipt")
    if isinstance(receipt, Mapping):
        data.update(
            {
                "block_number": receipt["block_number"],
                "receipt_status": receipt["status"],
                "gas_used": receipt["gas_used"],
                "effective_gas_price_wei": receipt[
                    "effective_gas_price_wei"
                ],
            }
        )
    error = result.get("error")
    return {
        "ok": bool(result.get("ok")),
        "state": state,
        "data": data,
        "error": error,
    }


def _receipt_data(client: Any, chain_id: int, transaction_hash: str) -> dict[str, Any]:
    from web3.exceptions import TransactionNotFound, Web3Exception

    try:
        receipt = client.eth.get_transaction_receipt(transaction_hash)
    except TransactionNotFound:
        receipt = None
    except (OSError, ValueError, Web3Exception) as exc:
        raise _RpcFailure("receipt") from exc
    if receipt is None:
        return {
            "chain_id": chain_id,
            "transaction_hash": transaction_hash,
            "state": "pending",
            "block_number": None,
            "status": None,
            "gas_used": None,
            "effective_gas_price_wei": None,
        }
    return _receipt_from_mapping(chain_id, transaction_hash, receipt)


def _receipt_from_mapping(
    chain_id: int,
    transaction_hash: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    status = int(receipt["status"])
    return {
        "chain_id": chain_id,
        "transaction_hash": transaction_hash,
        "state": "succeeded" if status == 1 else "reverted",
        "block_number": _decimal(receipt["blockNumber"]),
        "status": status,
        "gas_used": _decimal(receipt["gasUsed"]),
        "effective_gas_price_wei": _decimal(receipt["effectiveGasPrice"]),
    }


__all__ = [
    "inspect_blockchain",
    "preparation_digest",
    "prepare_transaction",
    "send_transaction",
]
