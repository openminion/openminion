from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from typing import Any, cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from .config import resolve_blockchain_config
from .abi import (
    abi_signature,
    decode_abi_values,
    decode_revert_fact,
    encode_function_call,
    normalize_abi_values,
    revert_data_from_exception,
    validate_abi_values,
)
from .schemas import (
    INSPECT_REQUEST_ADAPTER,
    PREPARE_REQUEST_ADAPTER,
    SEND_REQUEST_ADAPTER,
    CallContext,
    PreparedTransactionResult,
)
from .transaction_schemas import (
    RESOLVED_PREPARATION_ADAPTER,
    EqualityPostcondition,
    ResolvedPreparedTransactionResult,
    operation_digest,
    resolved_preparation_digest,
    validate_operation_record,
    validate_resolved_preparation_record,
)
from .preparations import (
    SessionRecordError,
    claim_operation_record,
    load_operation_record,
    load_resolved_preparation_record,
    replace_operation_record,
    save_prepared_transaction,
    save_resolved_preparation_record,
)
from .resolution import (
    ResolutionFailure,
    function_by_signature,
    load_resolution,
    revalidate_resolution,
    resolution_error_result,
    rpc_call,
)


class _RpcFailure(RuntimeError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(operation)


class _ChainMismatch(RuntimeError):
    def __init__(self, observed_chain_id: int) -> None:
        self.observed_chain_id = observed_chain_id
        super().__init__(str(observed_chain_id))


class _PreparationReverted(RuntimeError):
    def __init__(self, revert: dict[str, Any]) -> None:
        self.revert = revert
        super().__init__("prepare")


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


def _resolved_quantity(value: Any, operation: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid quantity.",
            {"operation": operation},
        )
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid quantity.",
            {"operation": operation},
        ) from exc


def _resolved_hex(value: Any, operation: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned invalid hexadecimal data.",
            {"operation": operation},
        )
    body = value[2:]
    if len(body) % 2 or any(
        character not in "0123456789abcdefABCDEF" for character in body
    ):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned invalid hexadecimal data.",
            {"operation": operation},
        )
    return f"0x{body.lower()}"


def _resolved_block(value: Any, operation: str) -> tuple[int, str]:
    if not isinstance(value, Mapping):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid block.",
            {"operation": operation},
        )
    number = _resolved_quantity(value.get("number"), operation)
    block_hash = _resolved_hex(value.get("hash"), operation)
    if len(block_hash) != 66:
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid block.",
            {"operation": operation},
        )
    return number, block_hash


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    host = str(parsed.hostname).lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
    return f"https://{netloc}"


def _resolved_error(code: str, message: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _error(code, message, details or {})


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
        validate_abi_values(request.function_args, request.function_abi.inputs)
        function = client.eth.contract(address=address, abi=[abi]).get_function_by_name(
            request.function_abi.name
        )(*request.function_args)
        raw = _rpc("chain_read", lambda: function.call(block_identifier="pending"))
        return {
            "chain_id": chain_id,
            "contract_address": address,
            "function_signature": abi_signature(request.function_abi),
            "return_values": normalize_abi_values(
                []
                if not request.function_abi.outputs
                else [raw]
                if len(request.function_abi.outputs) == 1
                else list(raw),
                request.function_abi.outputs,
                client,
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
            "to_address": client.to_checksum_address(to_address)
            if to_address
            else None,
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


def _recheck_resolved_block(
    record: Mapping[str, Any], block_number: int, block_hash: str
) -> None:
    observed_number, observed_hash = _resolved_block(
        rpc_call(
            record,
            "eth_getBlockByNumber",
            [hex(block_number), False],
        ),
        "eth_getBlockByNumber:recheck",
    )
    if observed_number != block_number or observed_hash != block_hash:
        raise ResolutionFailure(
            "STALE_BLOCK",
            "Pinned block changed during blockchain operation.",
        )


def _resolved_contract_call(
    request: Any, context: Any, *, block_number: int | None = None
) -> dict[str, Any]:
    from web3 import Web3

    record = load_resolution(request.resolution_digest, context)
    function = function_by_signature(record, request.function_signature)
    if function.stateMutability not in {"view", "pure"}:
        return _resolved_error(
            "INVALID_ARGUMENT",
            "Resolved contract read requires a view or pure function.",
            {"field": "function_signature"},
        )
    validate_abi_values(request.arguments, function.inputs)
    pinned = (
        revalidate_resolution(record)
        if block_number is None
        else {"block_number": str(block_number), "block_hash": ""}
    )
    selected_number = int(pinned["block_number"])
    data = encode_function_call(Web3(), function, request.arguments)
    raw_hex = _resolved_hex(
        rpc_call(
            record,
            "eth_call",
            [{"to": record["contract_address"], "data": data}, hex(selected_number)],
        ),
        "eth_call",
    )
    if block_number is None:
        _recheck_resolved_block(record, selected_number, pinned["block_hash"])
    raw = bytes.fromhex(raw_hex[2:])
    decoded = decode_abi_values(Web3(), function.outputs, raw) if function.outputs else []
    return {
        "ok": True,
        "state": "succeeded",
        "action": "resolved_contract_call",
        "data": {
            "resolution_digest": request.resolution_digest,
            "block_number": str(selected_number),
            "block_hash": pinned["block_hash"],
            "function_signature": request.function_signature,
            "arguments": normalize_abi_values(
                request.arguments,
                function.inputs,
                Web3(),
            ),
            "raw_return_digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
            "result": decoded,
        },
    }


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
    if (
        args.get("action")
        not in {"resolved_contract_call", "operation_status"}
        and (not config.rpc_url or config.chain_id is None)
    ):
        return _error(
            "FEATURE_UNAVAILABLE",
            "Configured blockchain network is unavailable.",
            {"feature": "configured_blockchain_network"},
        )
    try:
        request = INSPECT_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "request_schema"},
        )

    if request.action == "resolved_contract_call":
        try:
            return _resolved_contract_call(request, context)
        except SessionRecordError:
            return _resolved_error(
                "RESOLUTION_UNAVAILABLE",
                "Contract resolution is unavailable in this session.",
            )
        except ResolutionFailure as exc:
            return resolution_error_result(exc)
        except (TypeError, ValueError):
            return _resolved_error(
                "INVALID_ARGUMENT",
                "Blockchain arguments are invalid.",
                {"field": "", "reason": "call_encoding"},
            )
    if request.action == "operation_status":
        return _operation_status(request.preparation_digest, context)
    if not config.rpc_url or config.chain_id is None:
        return _error(
            "FEATURE_UNAVAILABLE",
            "Configured blockchain network is unavailable.",
            {"feature": "configured_blockchain_network"},
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
        data = encode_function_call(client, request.function_abi, request.function_args)
        call_context = CallContext(
            function_abi=request.function_abi,
            function_args=request.function_args,
            function_signature=abi_signature(request.function_abi),
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
    from web3.exceptions import ContractLogicError

    try:
        gas_limit = int(
            _rpc("estimate_gas", lambda: client.eth.estimate_gas(rpc_transaction))
        )
    except _RpcFailure as exc:
        if isinstance(exc.__cause__, ContractLogicError):
            raise _PreparationReverted(
                decode_revert_fact(
                    client,
                    revert_data_from_exception(exc.__cause__),
                )
            ) from exc
        raise
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


def _simulate_prepared_transaction(
    config: Any,
    request: Any,
    client: Any,
    normalized: dict[str, Any],
    call_context: dict[str, Any] | None,
    rpc_transaction: dict[str, Any],
    gas_limit: int,
    max_total_fee: int,
) -> dict[str, Any]:
    from eth_abi.exceptions import DecodingError
    from web3.exceptions import ContractLogicError, Web3Exception

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
        return_data = client.eth.call({**rpc_transaction, "gas": gas_limit}, "pending")
    except ContractLogicError as exc:
        return _error(
            "SIMULATION_REVERTED",
            "Transaction simulation reverted.",
            {
                "stage": "prepare",
                "revert": decode_revert_fact(client, revert_data_from_exception(exc)),
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
    if request.kind == "contract_call" and request.function_abi.outputs:
        try:
            decoded_returns = decode_abi_values(
                client, request.function_abi.outputs, bytes(return_data)
            )
        except (DecodingError, ValueError):
            decoded_returns = None
    return PreparedTransactionResult.model_validate(
        {
            "ok": True,
            "state": "prepared",
            "transaction": normalized,
            "call_context": call_context,
            "simulation": {
                "state": "succeeded",
                "chain_id": str(normalized["chain_id"]),
                "block_identifier": "pending",
                "resolved_block_number": None,
                "resolved_block_hash": None,
                "return_data": _hex_data(return_data).lower(),
                "gas_estimate": str(gas_limit),
                "decoded_returns": decoded_returns,
            },
            "preparation_digest": preparation_digest(normalized, call_context),
        }
    ).model_dump(mode="json")


def _load_resolved_account(config: Any, context: Any) -> Any:
    from eth_account import Account

    secret_service = getattr(context, "secret_service", None)
    if secret_service is None or not config.signer_secret_key:
        raise KeyError("signer unavailable")
    private_key = secret_service.get_secret_sync(
        config.signer_secret_key,
        namespace=config.signer_secret_namespace,
    )
    return Account.from_key(private_key)


def _validate_postconditions(
    record: Mapping[str, Any], postconditions: list[EqualityPostcondition]
) -> None:
    from web3 import Web3

    for postcondition in postconditions:
        function = function_by_signature(record, postcondition.function_signature)
        if function.stateMutability not in {"view", "pure"}:
            raise ValueError("postcondition must be read-only")
        validate_abi_values(postcondition.arguments, function.inputs)
        if (
            len(postcondition.expected_result) != len(function.outputs)
            or normalize_abi_values(
                postcondition.expected_result,
                function.outputs,
                Web3(),
            )
            != postcondition.expected_result
        ):
            raise ValueError("postcondition result length does not match")


def _resolved_rpc_transaction(
    transaction: Mapping[str, Any], *, include_gas: bool
) -> dict[str, Any]:
    payload = {
        "from": transaction["from_address"],
        "to": transaction["to_address"],
        "value": hex(int(transaction["value_wei"])),
        "nonce": hex(int(transaction["nonce"])),
        "data": transaction["data"],
    }
    if include_gas:
        payload["gas"] = hex(int(transaction["gas_limit"]))
    return payload


def _prepare_resolved_transaction(
    request: Any,
    config: Any,
    context: Any,
) -> dict[str, Any]:
    from web3 import Web3

    try:
        record = load_resolution(request.resolution_digest, context)
        pinned = revalidate_resolution(record)
        function = function_by_signature(record, request.function_signature)
        validate_abi_values(request.arguments, function.inputs)
        if function.stateMutability in {"view", "pure"}:
            raise ValueError("resolved preparation requires a state-changing function")
        _validate_postconditions(record, request.postconditions)
    except SessionRecordError:
        return _resolved_error(
            "RESOLUTION_UNAVAILABLE",
            "Contract resolution is unavailable in this session.",
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    except (TypeError, ValueError):
        return _resolved_error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "transaction_encoding"},
        )
    try:
        account = _load_resolved_account(config, context)
    except (KeyError, OSError, TypeError, ValueError):
        return _signer_unavailable()

    sender = Web3.to_checksum_address(account.address)
    block_number = int(pinned["block_number"])
    block_tag = hex(block_number)
    data = encode_function_call(Web3(), function, request.arguments)
    rpc_transaction = {
        "from": sender,
        "to": record["contract_address"],
        "value": hex(int(request.value_wei)),
        "data": data,
    }
    try:
        _resolved_quantity(
            rpc_call(record, "eth_getBalance", [sender, block_tag]),
            "eth_getBalance",
        )
        nonce = _resolved_quantity(
            rpc_call(record, "eth_getTransactionCount", [sender, "pending"]),
            "eth_getTransactionCount",
        )
        return_data = _resolved_hex(
            rpc_call(record, "eth_call", [rpc_transaction, block_tag]),
            "eth_call",
        )
        gas_limit = _resolved_quantity(
            rpc_call(record, "eth_estimateGas", [rpc_transaction, block_tag]),
            "eth_estimateGas",
        )
        block = rpc_call(record, "eth_getBlockByNumber", [block_tag, False])
        observed_number, observed_hash = _resolved_block(
            block, "eth_getBlockByNumber:fees"
        )
        if observed_number != block_number or observed_hash != pinned["block_hash"]:
            raise ResolutionFailure(
                "STALE_BLOCK",
                "Pinned block changed during transaction preparation.",
            )
        base_fee_value = block.get("baseFeePerGas")
        common = {
            "schema_version": "evm-transaction-v1",
            "chain_id": record["observed_chain_id"],
            "from_address": sender,
            "to_address": record["contract_address"],
            "value_wei": request.value_wei,
            "nonce": str(nonce),
            "gas_limit": str(gas_limit),
            "data": data,
        }
        if base_fee_value is None:
            gas_price = _resolved_quantity(
                rpc_call(record, "eth_gasPrice", []), "eth_gasPrice"
            )
            normalized = {
                **common,
                "transaction_type": "legacy",
                "gas_price_wei": str(gas_price),
                "max_total_fee_wei": str(gas_limit * gas_price),
            }
        else:
            base_fee = _resolved_quantity(base_fee_value, "baseFeePerGas")
            priority_fee = _resolved_quantity(
                rpc_call(record, "eth_maxPriorityFeePerGas", []),
                "eth_maxPriorityFeePerGas",
            )
            max_fee = 2 * base_fee + priority_fee
            normalized = {
                **common,
                "transaction_type": "eip1559",
                "max_fee_per_gas_wei": str(max_fee),
                "max_priority_fee_per_gas_wei": str(priority_fee),
                "max_total_fee_wei": str(gas_limit * max_fee),
            }
        if int(normalized["max_total_fee_wei"]) > int(config.max_total_fee_wei):
            return _resolved_error(
                "FEE_CAP_EXCEEDED",
                "Transaction fee exceeds the configured cap.",
                {
                    "max_total_fee_wei": normalized["max_total_fee_wei"],
                    "configured_max_total_fee_wei": str(config.max_total_fee_wei),
                },
            )
        _recheck_resolved_block(record, block_number, pinned["block_hash"])
    except ResolutionFailure as exc:
        return resolution_error_result(exc)

    raw_return = bytes.fromhex(return_data[2:])
    decoded_returns = None
    if function.outputs:
        try:
            decoded_returns = decode_abi_values(Web3(), function.outputs, raw_return)
        except ValueError:
            decoded_returns = None
    call_context = CallContext(
        function_abi=function,
        function_args=request.arguments,
        function_signature=request.function_signature,
    )
    resolved_context = {
        "resolution_digest": request.resolution_digest,
        "rpc_origin": _origin(record["rpc_url"]),
        "sourcify_target_origin": _origin(record["sourcify_target_url"]),
        "sourcify_implementation_origin": (
            _origin(record["sourcify_implementation_url"])
            if record["sourcify_implementation_url"]
            else None
        ),
        "expected_chain_id": record["expected_chain_id"],
        "observed_chain_id": record["observed_chain_id"],
        "expected_genesis_hash": record["expected_genesis_hash"],
        "observed_genesis_hash": record["observed_genesis_hash"],
        "expected_checkpoint": record["expected_checkpoint"],
        "observed_checkpoint": record["observed_checkpoint"],
        "resolution_block_number": record["verification_block_number"],
        "resolution_block_hash": record["verification_block_hash"],
        "preparation_block_number": str(block_number),
        "preparation_block_hash": pinned["block_hash"],
        "contract_address": record["contract_address"],
        "proxy_kind": record["proxy_kind"],
        "implementation_address": record["implementation_address"],
        "abi_address": record["abi_address"],
        "target_code_hash": record["target_code_hash"],
        "implementation_code_hash": record["implementation_code_hash"],
        "function_abi": function.model_dump(mode="json"),
    }
    prepared = {
        "schema_version": 2,
        "kind": "resolved_contract_call",
        "resolution_digest": request.resolution_digest,
        "resolved_context": resolved_context,
        "transaction": normalized,
        "call_context": call_context.model_dump(mode="json"),
        "simulation": {
            "state": "succeeded",
            "chain_id": str(record["observed_chain_id"]),
            "block_identifier": block_tag,
            "resolved_block_number": str(block_number),
            "resolved_block_hash": pinned["block_hash"],
            "return_data": return_data,
            "gas_estimate": str(gas_limit),
            "decoded_returns": decoded_returns,
        },
        "signer_address": sender,
        "postconditions": [
            postcondition.model_dump(mode="json")
            for postcondition in request.postconditions
        ],
        "preparation_digest": "sha256:" + "0" * 64,
    }
    prepared["preparation_digest"] = resolved_preparation_digest(prepared)
    result = ResolvedPreparedTransactionResult.model_validate(
        {**prepared, "ok": True, "state": "prepared"}
    ).model_dump(mode="json")
    try:
        save_resolved_preparation_record(
            prepared,
            context,
            validator=validate_resolved_preparation_record,
            digester=resolved_preparation_digest,
        )
    except SessionRecordError:
        return _resolved_error(
            "RESULT_TOO_LARGE",
            "Resolved preparation exceeds the persisted result limit.",
        )
    return result


def prepare_transaction(
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
    if (
        args.get("kind") != "resolved_contract_call"
        and (not config.rpc_url or config.chain_id is None)
    ):
        return _error(
            "FEATURE_UNAVAILABLE",
            "Configured blockchain network is unavailable.",
            {"feature": "configured_blockchain_network"},
        )
    try:
        request = PREPARE_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "request_schema"},
        )

    if request.kind == "resolved_contract_call":
        return _prepare_resolved_transaction(request, config, context)
    if not config.rpc_url or config.chain_id is None:
        return _error(
            "FEATURE_UNAVAILABLE",
            "Configured blockchain network is unavailable.",
            {"feature": "configured_blockchain_network"},
        )

    try:
        client, account = _load_signing_account(config, context, web3)
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
    except _PreparationReverted as exc:
        return _error(
            "SIMULATION_REVERTED",
            "Transaction simulation reverted.",
            {
                "stage": "prepare",
                "revert": exc.revert,
                "broadcast_attempted": False,
            },
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

    result = _simulate_prepared_transaction(
        config,
        request,
        client,
        normalized,
        call_context,
        rpc_transaction,
        gas_limit,
        max_total_fee,
    )
    if result.get("ok") is True:
        save_prepared_transaction(result, context)
    return result


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


def _load_signing_account(
    config: Any, context: Any, web3: Any | None
) -> tuple[Any, Any]:
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
    if _chain_id(client, int(config.chain_id)) != int(transaction["chain_id"]):
        stale_fields.append("chain_id")
    if int(client.eth.get_transaction_count(sender, "pending")) != int(
        transaction["nonce"]
    ):
        stale_fields.append("nonce")
    rpc_transaction = _rpc_transaction(transaction)
    if call_context is not None:
        try:
            encoded = encode_function_call(
                client,
                call_context.function_abi,
                call_context.function_args,
            )
        except (TypeError, ValueError):
            stale_fields.append("data")
        else:
            if encoded != transaction["data"]:
                stale_fields.append("data")
    if int(transaction["max_total_fee_wei"]) > int(config.max_total_fee_wei):
        stale_fields.append("fees")
    client.eth.call(rpc_transaction, "pending")
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


def _send_validated_transaction(
    context: Any,
    config: Any,
    request: Any,
    transaction: dict[str, Any],
    web3: Any | None,
) -> dict[str, Any]:
    from web3.exceptions import ContractLogicError, Web3Exception

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
    except ContractLogicError as exc:
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "failed",
            _error(
                "SIMULATION_REVERTED",
                "Transaction simulation reverted.",
                {
                    "stage": "send",
                    "revert": decode_revert_fact(
                        client, revert_data_from_exception(exc)
                    ),
                    "broadcast_attempted": False,
                },
            ),
        )
    except _ChainMismatch:
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "failed",
            _error(
                "STALE_PREPARATION",
                "Prepared transaction no longer matches chain state.",
                {"fields": ["chain_id"]},
            ),
        )
    except (OSError, ValueError, Web3Exception):
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


def _load_resolved_preparation(digest: str, context: Any) -> dict[str, Any]:
    return load_resolved_preparation_record(
        digest,
        session_id=str(getattr(context, "session_id", "") or ""),
        env=getattr(context, "env", None),
        validator=validate_resolved_preparation_record,
        digester=resolved_preparation_digest,
    )


def _resolved_send_failure(
    context: Any,
    request: Any,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _send_terminal(
        context,
        request.transaction.model_dump(mode="json"),
        request.preparation_digest,
        "failed",
        _resolved_error(code, message, details),
    )


def _resolution_matches_preparation(record: Mapping[str, Any], request: Any) -> bool:
    context = request.resolved_context
    expected = {
        "expected_chain_id": context.expected_chain_id,
        "observed_chain_id": context.observed_chain_id,
        "expected_genesis_hash": context.expected_genesis_hash,
        "observed_genesis_hash": context.observed_genesis_hash,
        "expected_checkpoint": (
            context.expected_checkpoint.model_dump(mode="json")
            if context.expected_checkpoint is not None
            else None
        ),
        "observed_checkpoint": (
            context.observed_checkpoint.model_dump(mode="json")
            if context.observed_checkpoint is not None
            else None
        ),
        "verification_block_number": context.resolution_block_number,
        "verification_block_hash": context.resolution_block_hash,
        "contract_address": context.contract_address,
        "proxy_kind": context.proxy_kind,
        "implementation_address": context.implementation_address,
        "abi_address": context.abi_address,
        "target_code_hash": context.target_code_hash,
        "implementation_code_hash": context.implementation_code_hash,
    }
    return all(record.get(key) == value for key, value in expected.items()) and (
        _origin(str(record.get("rpc_url", ""))) == context.rpc_origin
        and _origin(str(record.get("sourcify_target_url", "")))
        == context.sourcify_target_origin
        and (
            _origin(str(record["sourcify_implementation_url"]))
            if record.get("sourcify_implementation_url")
            else None
        )
        == context.sourcify_implementation_origin
    )


def _send_resolved_transaction(
    request: Any,
    context: Any,
    config: Any,
) -> dict[str, Any]:
    from web3 import Web3

    try:
        stored = _load_resolved_preparation(request.preparation_digest, context)
    except SessionRecordError:
        return _resolved_send_failure(
            context,
            request,
            "STALE_PREPARATION",
            "Resolved preparation is unavailable in this session.",
            {"fields": ["preparation"]},
        )
    if stored != request.model_dump(mode="json"):
        return _resolved_send_failure(
            context,
            request,
            "STALE_PREPARATION",
            "Approved preparation does not match the stored preparation.",
            {"fields": ["preparation"]},
        )
    try:
        record = load_resolution(request.resolution_digest, context)
        pinned = revalidate_resolution(record)
    except SessionRecordError:
        return _resolved_send_failure(
            context,
            request,
            "RESOLUTION_UNAVAILABLE",
            "Contract resolution is unavailable in this session.",
        )
    except ResolutionFailure as exc:
        return _resolved_send_failure(
            context,
            request,
            exc.code,
            exc.message,
            exc.details,
        )
    try:
        resolved_function = function_by_signature(
            record, request.call_context.function_signature
        )
    except ValueError:
        resolved_function = None
    if (
        not _resolution_matches_preparation(record, request)
        or resolved_function != request.call_context.function_abi
    ):
        return _resolved_send_failure(
            context,
            request,
            "STALE_RESOLUTION",
            "Resolved contract facts no longer match the approved preparation.",
        )
    try:
        account = _load_resolved_account(config, context)
    except (KeyError, OSError, TypeError, ValueError):
        return _resolved_send_failure(
            context,
            request,
            "SIGNER_UNAVAILABLE",
            "Configured blockchain signer is unavailable.",
        )
    sender = Web3.to_checksum_address(account.address)
    transaction = request.transaction.model_dump(mode="json")
    if int(transaction["max_total_fee_wei"]) > int(config.max_total_fee_wei):
        return _resolved_send_failure(
            context,
            request,
            "FEE_CAP_EXCEEDED",
            "Transaction fee exceeds the configured cap.",
            {
                "max_total_fee_wei": transaction["max_total_fee_wei"],
                "configured_max_total_fee_wei": str(config.max_total_fee_wei),
            },
        )
    if sender != request.signer_address or sender != transaction["from_address"]:
        return _resolved_send_failure(
            context,
            request,
            "SIGNER_MISMATCH",
            "Prepared sender does not match the configured signer.",
            {
                "expected_from_address": request.signer_address,
                "observed_from_address": sender,
            },
        )

    block_number = int(pinned["block_number"])
    block_tag = hex(block_number)
    try:
        _resolved_quantity(
            rpc_call(record, "eth_getBalance", [sender, block_tag]),
            "eth_getBalance",
        )
        nonce = _resolved_quantity(
            rpc_call(record, "eth_getTransactionCount", [sender, "pending"]),
            "eth_getTransactionCount",
        )
        if nonce != int(transaction["nonce"]):
            return _resolved_send_failure(
                context,
                request,
                "STALE_PREPARATION",
                "Prepared transaction no longer matches chain state.",
                {"fields": ["nonce"]},
            )
        simulated = _resolved_hex(
            rpc_call(
                record,
                "eth_call",
                [_resolved_rpc_transaction(transaction, include_gas=True), block_tag],
            ),
            "eth_call",
        )
        if simulated != request.simulation.return_data:
            return _resolved_send_failure(
                context,
                request,
                "STALE_PREPARATION",
                "Prepared transaction no longer matches chain state.",
                {"fields": ["simulation"]},
            )
        _recheck_resolved_block(record, block_number, pinned["block_hash"])
    except ResolutionFailure as exc:
        return _resolved_send_failure(
            context,
            request,
            exc.code,
            exc.message,
            exc.details,
        )

    signed = account.sign_transaction(
        {
            key: value
            for key, value in _rpc_transaction(transaction).items()
            if key != "from"
        }
    )
    raw_transaction = signed.raw_transaction
    transaction_hash = _hex_data(Web3.keccak(raw_transaction)).lower()
    operation = {
        "schema_version": 1,
        "preparation_digest": request.preparation_digest,
        "resolution_digest": request.resolution_digest,
        "operation_digest": "sha256:" + "0" * 64,
        "transaction_hash": transaction_hash,
        "submission_started": True,
        "broadcast_attempts": 1,
        "state": "broadcast_unknown",
        "receipt_block_number": None,
        "receipt_block_hash": None,
        "receipt_status": None,
        "confirmations": 0,
        "confirmation_depth": int(config.confirmation_depth),
        "gas_used": None,
        "effective_gas_price_wei": None,
        "postconditions": [
            postcondition.model_dump(mode="json")
            for postcondition in request.postconditions
        ],
        "postcondition_results": [],
        "last_error_code": None,
    }
    operation["operation_digest"] = operation_digest(operation)
    if not claim_operation_record(
        operation,
        context,
        validator=validate_operation_record,
        digester=operation_digest,
    ):
        return _resolved_send_failure(
            context,
            request,
            "OPERATION_INVALID",
            "This preparation already has a submission operation.",
            {"broadcast_attempted": False},
        )
    try:
        submitted_hash = _resolved_hex(
            rpc_call(
                record,
                "eth_sendRawTransaction",
                [_hex_data(raw_transaction)],
            ),
            "eth_sendRawTransaction",
        )
    except ResolutionFailure:
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "broadcast_unknown",
            _resolved_error(
                "BROADCAST_UNKNOWN",
                "Transaction submission outcome is unknown.",
                {"transaction_hash": transaction_hash, "broadcast_attempts": 1},
            ),
            transaction_hash=transaction_hash,
            broadcast_attempts=1,
        )
    if submitted_hash != transaction_hash:
        operation["last_error_code"] = "OPERATION_INVALID"
        replace_operation_record(
            operation,
            context,
            validator=validate_operation_record,
            digester=operation_digest,
        )
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "broadcast_unknown",
            _resolved_error(
                "OPERATION_INVALID",
                "RPC returned a transaction hash different from the signed transaction.",
                {"transaction_hash": transaction_hash},
            ),
            transaction_hash=transaction_hash,
            broadcast_attempts=1,
        )
    operation["state"] = "pending"
    replace_operation_record(
        operation,
        context,
        validator=validate_operation_record,
        digester=operation_digest,
    )
    return _send_terminal(
        context,
        transaction,
        request.preparation_digest,
        "pending",
        {"ok": True},
        transaction_hash=transaction_hash,
        broadcast_attempts=1,
    )


def _operation_result(operation: Mapping[str, Any]) -> dict[str, Any]:
    data = {
        key: operation[key]
        for key in (
            "preparation_digest",
            "resolution_digest",
            "operation_digest",
            "transaction_hash",
            "submission_started",
            "broadcast_attempts",
            "state",
            "receipt_block_number",
            "receipt_block_hash",
            "receipt_status",
            "confirmations",
            "confirmation_depth",
            "gas_used",
            "effective_gas_price_wei",
            "postcondition_results",
            "last_error_code",
        )
    }
    return {
        "ok": True,
        "state": "succeeded",
        "action": "operation_status",
        "data": data,
    }


def _persist_operation(operation: Mapping[str, Any], context: Any) -> dict[str, Any]:
    replace_operation_record(
        operation,
        context,
        validator=validate_operation_record,
        digester=operation_digest,
    )
    return _operation_result(operation)


def _postcondition_results(
    operation: Mapping[str, Any],
    record: Mapping[str, Any],
    block_number: int,
) -> list[dict[str, Any]]:
    from web3 import Web3

    results: list[dict[str, Any]] = []
    for raw_postcondition in operation["postconditions"]:
        postcondition = EqualityPostcondition.model_validate(raw_postcondition)
        actual: list[Any] | None = None
        error_code: str | None = None
        try:
            function = function_by_signature(
                record, postcondition.function_signature
            )
            call_data = encode_function_call(
                Web3(), function, postcondition.arguments
            )
            raw_result = _resolved_hex(
                rpc_call(
                    record,
                    "eth_call",
                    [
                        {"to": record["contract_address"], "data": call_data},
                        hex(block_number),
                    ],
                ),
                "eth_call:postcondition",
            )
            actual = (
                decode_abi_values(
                    Web3(), function.outputs, bytes.fromhex(raw_result[2:])
                )
                if function.outputs
                else []
            )
        except ResolutionFailure as exc:
            error_code = exc.code
        except (TypeError, ValueError):
            error_code = "OPERATION_INVALID"
        matched = error_code is None and actual == postcondition.expected_result
        results.append(
            {
                **postcondition.model_dump(mode="json"),
                "actual_result": actual,
                "matched": matched,
                "error_code": error_code,
            }
        )
    return results


def _operation_status(preparation_digest_value: str, context: Any) -> dict[str, Any]:
    try:
        operation = load_operation_record(
            preparation_digest_value,
            session_id=str(getattr(context, "session_id", "") or ""),
            env=getattr(context, "env", None),
            validator=validate_operation_record,
            digester=operation_digest,
        )
    except SessionRecordError:
        return _resolved_error(
            "OPERATION_UNAVAILABLE",
            "Blockchain operation is unavailable in this session.",
        )
    try:
        preparation = _load_resolved_preparation(preparation_digest_value, context)
    except SessionRecordError:
        return _resolved_error(
            "OPERATION_INVALID",
            "Stored operation preparation is unavailable.",
        )
    prepared_model = RESOLVED_PREPARATION_ADAPTER.validate_python(preparation)
    try:
        record = load_resolution(operation["resolution_digest"], context)
        pinned = revalidate_resolution(record)
    except SessionRecordError:
        return _resolved_error(
            "RESOLUTION_UNAVAILABLE",
            "Contract resolution is unavailable in this session.",
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)

    if preparation["resolution_digest"] != operation["resolution_digest"]:
        return _resolved_error(
            "OPERATION_INVALID",
            "Stored operation does not match its preparation.",
        )
    if not _resolution_matches_preparation(record, prepared_model):
        return _resolved_error(
            "STALE_RESOLUTION",
            "Resolved contract facts no longer match the operation.",
        )
    head_number = int(pinned["block_number"])
    try:
        receipt = rpc_call(
            record,
            "eth_getTransactionReceipt",
            [operation["transaction_hash"]],
        )
        if receipt is None:
            if operation["receipt_block_number"] is not None:
                operation["state"] = "reorged"
                operation["last_error_code"] = None
            elif operation["state"] != "broadcast_unknown":
                operation["state"] = "pending"
                operation["last_error_code"] = None
            return _persist_operation(operation, context)
        if not isinstance(receipt, Mapping):
            raise ResolutionFailure(
                "RPC_UNAVAILABLE",
                "Blockchain RPC returned an invalid receipt.",
                {"operation": "eth_getTransactionReceipt"},
            )
        returned_hash = receipt.get("transactionHash")
        if returned_hash is not None and (
            not isinstance(returned_hash, str)
            or returned_hash.lower() != operation["transaction_hash"]
        ):
            return _resolved_error(
                "OPERATION_INVALID",
                "Receipt transaction hash does not match the stored operation.",
            )
        receipt_number = _resolved_quantity(
            receipt.get("blockNumber"), "receipt:blockNumber"
        )
        receipt_hash = _resolved_hex(receipt.get("blockHash"), "receipt:blockHash")
        receipt_status = _resolved_quantity(receipt.get("status"), "receipt:status")
        if receipt_status not in {0, 1}:
            raise ResolutionFailure(
                "RPC_UNAVAILABLE",
                "Blockchain RPC returned an invalid receipt status.",
                {"operation": "eth_getTransactionReceipt"},
            )
        canonical_block = rpc_call(
            record,
            "eth_getBlockByNumber",
            [hex(receipt_number), False],
        )
        if canonical_block is None:
            canonical_number, canonical_hash = -1, ""
        else:
            canonical_number, canonical_hash = _resolved_block(
                canonical_block,
                "eth_getBlockByNumber:receipt",
            )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)

    prior_number = operation["receipt_block_number"]
    prior_hash = operation["receipt_block_hash"]
    if (
        receipt_number > head_number
        or canonical_number != receipt_number
        or canonical_hash != receipt_hash
        or (prior_number is not None and int(prior_number) != receipt_number)
        or (prior_hash is not None and prior_hash != receipt_hash)
    ):
        operation["state"] = "reorged"
        operation["last_error_code"] = None
        return _persist_operation(operation, context)

    confirmations = head_number - receipt_number + 1
    try:
        gas_used = (
            str(_resolved_quantity(receipt["gasUsed"], "receipt:gasUsed"))
            if receipt.get("gasUsed") is not None
            else None
        )
        effective_gas_price = (
            str(
                _resolved_quantity(
                    receipt["effectiveGasPrice"],
                    "receipt:effectiveGasPrice",
                )
            )
            if receipt.get("effectiveGasPrice") is not None
            else None
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    operation.update(
        {
            "state": "confirming",
            "receipt_block_number": str(receipt_number),
            "receipt_block_hash": receipt_hash,
            "receipt_status": receipt_status,
            "confirmations": confirmations,
            "gas_used": gas_used,
            "effective_gas_price_wei": effective_gas_price,
            "postcondition_results": [],
            "last_error_code": None,
        }
    )
    if confirmations < int(operation["confirmation_depth"]):
        return _persist_operation(operation, context)

    if receipt_status == 0:
        terminal_state = "reverted"
    else:
        results = _postcondition_results(operation, record, receipt_number)
        operation["postcondition_results"] = results
        terminal_state = (
            "succeeded"
            if all(result["matched"] for result in results)
            else "postcondition_failed"
        )
    try:
        _recheck_resolved_block(record, head_number, pinned["block_hash"])
    except ResolutionFailure:
        operation["state"] = "confirming"
        operation["last_error_code"] = "STALE_BLOCK"
        operation["postcondition_results"] = []
        return _persist_operation(operation, context)
    operation["state"] = terminal_state
    operation["last_error_code"] = (
        "POSTCONDITION_FAILED" if terminal_state == "postcondition_failed" else None
    )
    return _persist_operation(operation, context)


def send_transaction(
    args: Mapping[str, Any],
    context: Any,
    *,
    web3: Any | None = None,
) -> dict[str, Any]:
    config = resolve_blockchain_config(context)
    if not config.enabled or not config.writes_enabled:
        return _error(
            "FEATURE_DISABLED",
            "Blockchain capability is disabled.",
            {"feature": "blockchain_writes"},
        )
    if (
        args.get("kind") != "resolved_contract_call"
        and (not config.rpc_url or config.chain_id is None)
    ):
        return _error(
            "FEATURE_UNAVAILABLE",
            "Configured blockchain network is unavailable.",
            {"feature": "configured_blockchain_network"},
        )
    try:
        request = (
            RESOLVED_PREPARATION_ADAPTER.validate_python(dict(args))
            if args.get("kind") == "resolved_contract_call"
            else SEND_REQUEST_ADAPTER.validate_python(dict(args))
        )
    except ValidationError:
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "request_schema"},
        )
    if getattr(context, "policy_authorization", None) is None:
        return _error(
            "POLICY_MODE_UNSUPPORTED",
            "Enforcing policy is required for blockchain send.",
            {"mode": "unavailable"},
        )
    if getattr(request, "kind", None) == "resolved_contract_call":
        return _send_resolved_transaction(request, context, config)
    if not config.rpc_url or config.chain_id is None:
        return _error(
            "FEATURE_UNAVAILABLE",
            "Configured blockchain network is unavailable.",
            {"feature": "configured_blockchain_network"},
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
    return _send_validated_transaction(context, config, request, transaction, web3)


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
                "effective_gas_price_wei": receipt["effective_gas_price_wei"],
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
