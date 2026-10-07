from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any

from eth_abi.exceptions import DecodingError

from .abi import (
    decode_abi_values,
    encode_function_call,
    normalize_abi_values,
    validate_abi_values,
)
from .preparations import (
    SessionRecordError,
    save_resolved_preparation_record,
)
from .resolution import (
    ResolutionFailure,
    _block,
    _hex_data,
    _quantity,
    _url_origin,
    function_by_signature,
    load_resolution,
    revalidate_resolution,
    resolution_error_result,
    rpc_call,
)
from .schemas import CallContext
from .transaction_schemas import (
    EqualityPostcondition,
    ResolvedPreparedTransactionResult,
    resolved_preparation_digest,
    validate_resolved_preparation_record,
)
from .runtime import _error


def _recheck_pinned_block(
    record: Mapping[str, Any], block_number: int, block_hash: str
) -> None:
    observed_number, observed_hash = _block(
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


def inspect_resolved_contract(
    request: Any, context: Any, *, block_number: int | None = None
) -> dict[str, Any]:
    from web3 import Web3

    try:
        record = load_resolution(request.resolution_digest, context)
        function = function_by_signature(record, request.function_signature)
        if function.stateMutability not in {"view", "pure"}:
            return _error(
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
        raw_hex = _hex_data(
            rpc_call(
                record,
                "eth_call",
                [
                    {"to": record["contract_address"], "data": data},
                    hex(selected_number),
                ],
            ),
            "eth_call",
        )
        if block_number is None:
            _recheck_pinned_block(record, selected_number, pinned["block_hash"])
    except SessionRecordError:
        return _error(
            "RESOLUTION_UNAVAILABLE",
            "Contract resolution is unavailable in this session.",
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    except (TypeError, ValueError):
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "call_encoding"},
        )
    raw = bytes.fromhex(raw_hex[2:])
    try:
        decoded = (
            decode_abi_values(Web3(), function.outputs, raw) if function.outputs else []
        )
    except DecodingError:
        return _error(
            "ABI_DECODE_FAILED",
            "Resolved contract return data could not be decoded.",
            {"function_signature": request.function_signature},
        )
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


def _load_preparation_facts(
    request: Any, context: Any
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    record = load_resolution(request.resolution_digest, context)
    pinned = revalidate_resolution(record)
    function = function_by_signature(record, request.function_signature)
    validate_abi_values(request.arguments, function.inputs)
    if function.stateMutability in {"view", "pure"}:
        raise ValueError("resolved preparation requires a state-changing function")
    _validate_postconditions(record, request.postconditions)
    return record, pinned, function


def _observe_preparation_state(
    request: Any,
    record: Mapping[str, Any],
    pinned: Mapping[str, Any],
    function: Any,
    sender: str,
) -> tuple[dict[str, Any], str, int, int, str]:
    from web3 import Web3

    block_number = int(pinned["block_number"])
    block_tag = hex(block_number)
    data = encode_function_call(Web3(), function, request.arguments)
    rpc_transaction = {
        "from": sender,
        "to": record["contract_address"],
        "value": hex(int(request.value_wei)),
        "data": data,
    }
    balance = _quantity(
        rpc_call(record, "eth_getBalance", [sender, "pending"]),
        "eth_getBalance",
    )
    nonce = _quantity(
        rpc_call(record, "eth_getTransactionCount", [sender, "pending"]),
        "eth_getTransactionCount",
    )
    return_data = _hex_data(
        rpc_call(record, "eth_call", [rpc_transaction, block_tag]),
        "eth_call",
    )
    gas_limit = _quantity(
        rpc_call(record, "eth_estimateGas", [rpc_transaction, block_tag]),
        "eth_estimateGas",
    )
    block = rpc_call(record, "eth_getBlockByNumber", [block_tag, False])
    observed_number, observed_hash = _block(block, "eth_getBlockByNumber:fees")
    if observed_number != block_number or observed_hash != pinned["block_hash"]:
        raise ResolutionFailure(
            "STALE_BLOCK",
            "Pinned block changed during transaction preparation.",
        )
    transaction = {
        "schema_version": "evm-transaction-v1",
        "chain_id": record["observed_chain_id"],
        "from_address": sender,
        "to_address": record["contract_address"],
        "value_wei": request.value_wei,
        "nonce": str(nonce),
        "gas_limit": str(gas_limit),
        "data": data,
    }
    base_fee_value = block.get("baseFeePerGas")
    if base_fee_value is None:
        gas_price = _quantity(rpc_call(record, "eth_gasPrice", []), "eth_gasPrice")
        transaction.update(
            transaction_type="legacy",
            gas_price_wei=str(gas_price),
            max_total_fee_wei=str(gas_limit * gas_price),
        )
    else:
        base_fee = _quantity(base_fee_value, "baseFeePerGas")
        priority_fee = _quantity(
            rpc_call(record, "eth_maxPriorityFeePerGas", []),
            "eth_maxPriorityFeePerGas",
        )
        max_fee = 2 * base_fee + priority_fee
        transaction.update(
            transaction_type="eip1559",
            max_fee_per_gas_wei=str(max_fee),
            max_priority_fee_per_gas_wei=str(priority_fee),
            max_total_fee_wei=str(gas_limit * max_fee),
        )
    return transaction, return_data, gas_limit, balance, block_tag


def _preparation_budget_error(
    transaction: Mapping[str, Any], balance: int, request: Any, config: Any
) -> dict[str, Any] | None:
    if int(transaction["max_total_fee_wei"]) > int(config.max_total_fee_wei):
        return _error(
            "FEE_CAP_EXCEEDED",
            "Transaction fee exceeds the configured cap.",
            {
                "max_total_fee_wei": transaction["max_total_fee_wei"],
                "configured_max_total_fee_wei": str(config.max_total_fee_wei),
            },
        )
    required_balance = int(request.value_wei) + int(transaction["max_total_fee_wei"])
    if balance < required_balance:
        return _error(
            "INSUFFICIENT_FUNDS",
            "Signer balance cannot cover transaction value and maximum fee.",
            {
                "balance_wei": str(balance),
                "required_balance_wei": str(required_balance),
            },
        )
    return None


def _build_resolved_preparation(
    request: Any,
    record: Mapping[str, Any],
    pinned: Mapping[str, Any],
    function: Any,
    transaction: Mapping[str, Any],
    return_data: str,
    gas_limit: int,
    block_tag: str,
    sender: str,
) -> dict[str, Any]:
    from web3 import Web3

    decoded_returns = None
    if function.outputs:
        try:
            decoded_returns = decode_abi_values(
                Web3(), function.outputs, bytes.fromhex(return_data[2:])
            )
        except (DecodingError, ValueError):
            decoded_returns = None
    call_context = CallContext(
        function_abi=function,
        function_args=request.arguments,
        function_signature=request.function_signature,
    )
    resolved_context = {
        "resolution_digest": request.resolution_digest,
        "rpc_origin": _url_origin(record["rpc_url"]),
        "sourcify_target_origin": _url_origin(record["sourcify_target_url"]),
        "sourcify_implementation_origin": (
            _url_origin(record["sourcify_implementation_url"])
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
        "preparation_block_number": str(pinned["block_number"]),
        "preparation_block_hash": pinned["block_hash"],
        "contract_address": record["contract_address"],
        "proxy_kind": record["proxy_kind"],
        "implementation_address": record["implementation_address"],
        "abi_address": record["abi_address"],
        "target_code_hash": record["target_code_hash"],
        "implementation_code_hash": record["implementation_code_hash"],
        "function_abi": function.model_dump(mode="json"),
    }
    return {
        "schema_version": 2,
        "kind": "resolved_contract_call",
        "resolution_digest": request.resolution_digest,
        "resolved_context": resolved_context,
        "transaction": dict(transaction),
        "call_context": call_context.model_dump(mode="json"),
        "simulation": {
            "state": "succeeded",
            "chain_id": str(record["observed_chain_id"]),
            "block_identifier": block_tag,
            "resolved_block_number": str(pinned["block_number"]),
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


def prepare_resolved_transaction(
    request: Any,
    config: Any,
    context: Any,
) -> dict[str, Any]:
    from web3 import Web3

    try:
        record, pinned, function = _load_preparation_facts(request, context)
    except SessionRecordError:
        return _error(
            "RESOLUTION_UNAVAILABLE",
            "Contract resolution is unavailable in this session.",
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    except (TypeError, ValueError):
        return _error(
            "INVALID_ARGUMENT",
            "Blockchain arguments are invalid.",
            {"field": "", "reason": "transaction_encoding"},
        )
    try:
        account = _load_resolved_account(config, context)
    except (KeyError, OSError, TypeError, ValueError):
        return _error(
            "SIGNER_UNAVAILABLE",
            "Configured blockchain signer is unavailable.",
        )

    sender = Web3.to_checksum_address(account.address)
    try:
        transaction, return_data, gas_limit, balance, block_tag = (
            _observe_preparation_state(request, record, pinned, function, sender)
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    budget_error = _preparation_budget_error(transaction, balance, request, config)
    if budget_error is not None:
        return budget_error
    try:
        _recheck_pinned_block(
            record, int(pinned["block_number"]), str(pinned["block_hash"])
        )
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    prepared = _build_resolved_preparation(
        request,
        record,
        pinned,
        function,
        transaction,
        return_data,
        gas_limit,
        block_tag,
        sender,
    )
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
    except (OSError, SessionRecordError) as exc:
        reason = exc.reason if isinstance(exc, SessionRecordError) else "unavailable"
        if reason == "size_limit":
            return _error(
                "RESULT_TOO_LARGE",
                "Resolved preparation exceeds the persisted result limit.",
            )
        return _error(
            "INVALID_ARGUMENT",
            "Resolved preparation could not be stored for this session.",
            {"reason": reason},
        )
    return result
