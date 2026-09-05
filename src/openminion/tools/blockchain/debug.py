from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any, cast

from eth_abi.exceptions import DecodingError
from pydantic import ValidationError

from .abi import (
    abi_selector,
    abi_signature,
    decode_abi_values,
    decode_revert_fact,
    encode_function_call,
    event_topic,
    revert_data_from_exception,
)
from .config import resolve_blockchain_config
from .debug_results import DEBUG_RESULT_ADAPTER
from .debug_schemas import DEBUG_REQUEST_ADAPTER, DecodeCalldataArgs, DecodeRevertArgs
from .runtime import (
    _ChainMismatch,
    _RpcFailure,
    _chain_id,
    _client,
    _error,
    _hex_data,
)
from .schema_types import AbiParameter, ErrorAbi

MAX_DEBUG_BYTES = 65536
MAX_TRANSACTION_EVENTS = 100


def _size(value: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    )


def _bounded(result: dict[str, Any]) -> dict[str, Any]:
    validated = DEBUG_RESULT_ADAPTER.validate_python(result)
    payload = cast(
        dict[str, Any], DEBUG_RESULT_ADAPTER.dump_python(validated, mode="json")
    )
    observed = _size(payload)
    if observed <= MAX_DEBUG_BYTES:
        return payload
    limit_error = _error(
        "RESULT_LIMIT_EXCEEDED",
        "Blockchain diagnostic result exceeds the supported limit.",
        {
            "surface": "debug_result",
            "limit": MAX_DEBUG_BYTES,
            "observed_at_least": observed,
        },
    )
    return cast(
        dict[str, Any],
        DEBUG_RESULT_ADAPTER.dump_python(
            DEBUG_RESULT_ADAPTER.validate_python(limit_error),
            mode="json",
        ),
    )


def _decode_error(
    operation: str, data: str, *, log_index: str | None = None
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "operation": operation,
        "data_sha256": hashlib.sha256(bytes.fromhex(data[2:])).hexdigest(),
    }
    if log_index is not None:
        details["log_index"] = log_index
    return _error(
        "ABI_DECODE_FAILED",
        "Blockchain ABI data could not be decoded.",
        details,
    )


def _resolved_block(
    client: Any, block_identifier: str
) -> tuple[str | None, str | None]:
    if block_identifier == "pending":
        return None, None
    value: str | int = (
        int(block_identifier) if block_identifier.isdecimal() else block_identifier
    )
    block = client.eth.get_block(value)
    return str(int(block["number"])), _hex_data(block["hash"]).lower()


def _simulate(request: Any, client: Any, chain_id: int) -> dict[str, Any]:
    from web3.exceptions import ContractLogicError, Web3Exception

    data = request.data.lower()
    if request.function_abi is not None:
        try:
            expected = encode_function_call(
                client, request.function_abi, request.function_args or []
            )
        except (TypeError, ValueError):
            return _decode_error("decode_calldata", data)
        if expected != data:
            return _decode_error("decode_calldata", data)
    transaction = {
        "from": client.to_checksum_address(request.from_address),
        "to": client.to_checksum_address(request.to_address),
        "data": data,
        "value": int(request.value_wei),
    }
    block: str | int = (
        int(request.block_identifier)
        if request.block_identifier.isdecimal()
        else request.block_identifier
    )
    try:
        return_data = client.eth.call(transaction, block)
        gas_estimate = client.eth.estimate_gas(transaction, block)
        resolved_number, resolved_hash = _resolved_block(
            client, request.block_identifier
        )
    except ContractLogicError as exc:
        return _error(
            "SIMULATION_REVERTED",
            "Transaction simulation reverted.",
            {
                "stage": "debug",
                "revert": decode_revert_fact(
                    client,
                    revert_data_from_exception(exc),
                    cast(Sequence[ErrorAbi], request.error_abis),
                ),
                "broadcast_attempted": False,
            },
        )
    except (OSError, ValueError, Web3Exception):
        return _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": "simulate"},
        )
    decoded_returns = None
    if request.function_abi is not None and request.function_abi.outputs:
        try:
            decoded_returns = decode_abi_values(
                client,
                request.function_abi.outputs,
                bytes(return_data),
            )
        except (DecodingError, ValueError):
            return _decode_error("decode_calldata", _hex_data(return_data).lower())
    return {
        "ok": True,
        "state": "succeeded",
        "action": "simulate_call",
        "data": {
            "chain_id": str(chain_id),
            "block_identifier": request.block_identifier,
            "resolved_block_number": resolved_number,
            "resolved_block_hash": resolved_hash,
            "return_data": _hex_data(return_data).lower(),
            "gas_estimate": str(int(gas_estimate)),
            "decoded_returns": decoded_returns,
        },
    }


def _decode_calldata(request: Any, client: Any) -> dict[str, Any]:
    data = request.data.lower()
    if data[:10] != abi_selector(request.function_abi, client):
        return _decode_error("decode_calldata", data)
    try:
        arguments = decode_abi_values(
            client, request.function_abi.inputs, bytes.fromhex(data[10:])
        )
    except (DecodingError, ValueError):
        return _decode_error("decode_calldata", data)
    return {
        "ok": True,
        "state": "succeeded",
        "action": "decode_calldata",
        "function_signature": abi_signature(request.function_abi),
        "arguments": arguments,
    }


def _indexed_value(parameter: AbiParameter, topic: str, client: Any) -> tuple[str, Any]:
    base_type = parameter.type.split("[", 1)[0]
    if base_type in {"string", "bytes", "tuple"} or "[" in parameter.type:
        return "keccak256", topic
    return "value", decode_abi_values(client, [parameter], bytes.fromhex(topic[2:]))[0]


def _event_arguments(
    event_abi: Any, log: Mapping[str, Any], client: Any
) -> list[dict[str, Any]]:
    indexed = [item for item in event_abi.inputs if item.indexed]
    unindexed = [item for item in event_abi.inputs if not item.indexed]
    topics = [_hex_data(topic).lower() for topic in log["topics"]][1:]
    if len(topics) != len(indexed):
        raise ValueError("event topic count does not match")
    unindexed_values = decode_abi_values(
        client,
        unindexed,
        bytes.fromhex(_hex_data(log["data"])[2:]),
    )
    indexed_values = iter(topics)
    unindexed_iter = iter(unindexed_values)
    arguments: list[dict[str, Any]] = []
    for parameter in event_abi.inputs:
        if parameter.indexed:
            value_kind, value = _indexed_value(parameter, next(indexed_values), client)
        else:
            value_kind, value = "value", next(unindexed_iter)
        arguments.append(
            {
                "name": parameter.name,
                "indexed": parameter.indexed,
                "value_kind": value_kind,
                "value": value,
            }
        )
    return arguments


def _transaction_events(request: Any, client: Any, chain_id: int) -> dict[str, Any]:
    from web3.exceptions import TransactionNotFound, Web3Exception

    try:
        client.eth.get_transaction(request.transaction_hash)
    except TransactionNotFound:
        return _error(
            "TRANSACTION_NOT_FOUND",
            "Blockchain transaction was not found.",
            {
                "operation": "transaction_events",
                "transaction_hash": request.transaction_hash,
            },
        )
    except (OSError, ValueError, Web3Exception):
        return _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": "receipt"},
        )
    try:
        receipt = client.eth.get_transaction_receipt(request.transaction_hash)
    except TransactionNotFound:
        return _error(
            "RECEIPT_PENDING",
            "Transaction was accepted but is not yet mined.",
            {"transaction_hash": request.transaction_hash, "accepted": True},
        )
    except (OSError, ValueError, Web3Exception):
        return _error(
            "RPC_UNAVAILABLE",
            "Blockchain RPC operation failed.",
            {"operation": "receipt"},
        )
    signature = abi_signature(request.event_abi)
    topic_zero = event_topic(request.event_abi, client).lower()
    contract_address = (
        client.to_checksum_address(request.contract_address)
        if request.contract_address is not None
        else None
    )
    matching = [
        log
        for log in receipt["logs"]
        if log.get("topics")
        and _hex_data(log["topics"][0]).lower() == topic_zero
        and (
            contract_address is None
            or client.to_checksum_address(log["address"]) == contract_address
        )
    ]
    if len(matching) > MAX_TRANSACTION_EVENTS:
        return _error(
            "RESULT_LIMIT_EXCEEDED",
            "Blockchain diagnostic result exceeds the supported limit.",
            {
                "surface": "transaction_events",
                "limit": MAX_TRANSACTION_EVENTS,
                "observed_at_least": MAX_TRANSACTION_EVENTS + 1,
            },
        )
    events: list[dict[str, Any]] = []
    for log in sorted(matching, key=lambda item: int(item["logIndex"])):
        log_index = str(int(log["logIndex"]))
        try:
            arguments = _event_arguments(request.event_abi, log, client)
        except (DecodingError, ValueError):
            return _decode_error(
                "transaction_events",
                _hex_data(log["data"]).lower(),
                log_index=log_index,
            )
        events.append(
            {
                "contract_address": client.to_checksum_address(log["address"]),
                "log_index": log_index,
                "arguments": arguments,
            }
        )
    return {
        "ok": True,
        "state": "succeeded",
        "action": "transaction_events",
        "chain_id": str(chain_id),
        "transaction_hash": request.transaction_hash.lower(),
        "receipt_block_number": str(int(receipt["blockNumber"])),
        "event_signature": signature,
        "events": events,
    }


def debug_blockchain(
    args: Mapping[str, Any],
    context: Any | None,
    *,
    web3: Any | None = None,
) -> dict[str, Any]:
    config = resolve_blockchain_config(context)
    if not config.enabled:
        return _bounded(
            _error(
                "FEATURE_DISABLED",
                "Blockchain capability is disabled.",
                {"feature": "blockchain"},
            )
        )
    try:
        request = DEBUG_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError:
        return _bounded(
            _error(
                "INVALID_ARGUMENT",
                "Blockchain arguments are invalid.",
                {"field": "", "reason": "request_schema"},
            )
        )
    request_payload = request.model_dump(mode="json")
    request_size = _size(request_payload)
    if request_size > MAX_DEBUG_BYTES:
        return _bounded(
            _error(
                "INVALID_ARGUMENT",
                "Blockchain arguments are invalid.",
                {"field": "", "reason": "debug_request_size"},
            )
        )

    if isinstance(request, (DecodeCalldataArgs, DecodeRevertArgs)):
        if web3 is None:
            from web3 import Web3

            web3 = Web3()
        result = (
            _decode_calldata(request, web3)
            if isinstance(request, DecodeCalldataArgs)
            else {
                "ok": True,
                "state": "succeeded",
                "action": "decode_revert",
                "revert": decode_revert_fact(web3, request.data, request.error_abis),
            }
        )
        return _bounded(result)

    client = web3 or _client(config.rpc_url)
    try:
        chain_id = _chain_id(client, cast(int, config.chain_id))
    except _ChainMismatch as exc:
        return _bounded(
            _error(
                "CHAIN_MISMATCH",
                "Configured and observed chain IDs differ.",
                {
                    "expected_chain_id": config.chain_id,
                    "observed_chain_id": exc.observed_chain_id,
                },
            )
        )
    except _RpcFailure:
        return _bounded(
            _error(
                "RPC_UNAVAILABLE",
                "Blockchain RPC operation failed.",
                {
                    "operation": (
                        "simulate" if request.action == "simulate_call" else "receipt"
                    )
                },
            )
        )

    if request.action == "simulate_call":
        result = _simulate(request, client, chain_id)
    else:
        result = _transaction_events(request, client, chain_id)
    return _bounded(result)
