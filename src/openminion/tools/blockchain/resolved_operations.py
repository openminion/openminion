from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from eth_abi.exceptions import DecodingError

from .abi import decode_abi_values, encode_function_call
from .preparations import (
    MAX_OPERATION_RECORD_BYTES,
    SessionRecordError,
    claim_operation_record,
    load_operation_record,
    load_resolved_preparation_record,
    replace_operation_record,
)
from .resolution import (
    ResolutionFailure,
    _block,
    _hex_data,
    _quantity,
    _url_origin,
    function_by_signature,
    load_resolution,
    revalidate_chain_identity,
    revalidate_resolution,
    resolution_error_result,
    resolution_record_error_result,
    rpc_call,
)
from .resolved_calls import (
    _load_resolved_account,
    _recheck_pinned_block,
    _resolved_rpc_transaction,
)
from .runtime import _error, _rpc_transaction, _send_terminal
from .schema_types import web3_hex_data
from .transaction_schemas import (
    RESOLVED_PREPARATION_ADAPTER,
    EqualityPostcondition,
    operation_digest,
    resolved_preparation_digest,
    validate_operation_record,
    validate_resolved_preparation_record,
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
        _error(code, message, details),
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
        _url_origin(str(record.get("rpc_url", ""))) == context.rpc_origin
        and _url_origin(str(record.get("sourcify_target_url", "")))
        == context.sourcify_target_origin
        and (
            _url_origin(str(record["sourcify_implementation_url"]))
            if record.get("sourcify_implementation_url")
            else None
        )
        == context.sourcify_implementation_origin
    )


def _load_validated_send_resolution(
    request: Any, context: Any
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    try:
        stored = _load_resolved_preparation(request.preparation_digest, context)
    except SessionRecordError:
        return (
            None,
            None,
            _resolved_send_failure(
                context,
                request,
                "STALE_PREPARATION",
                "Resolved preparation is unavailable in this session.",
                {"fields": ["preparation"]},
            ),
        )
    if stored != request.model_dump(mode="json"):
        return (
            None,
            None,
            _resolved_send_failure(
                context,
                request,
                "STALE_PREPARATION",
                "Approved preparation does not match the stored preparation.",
                {"fields": ["preparation"]},
            ),
        )
    try:
        record = load_resolution(request.resolution_digest, context)
        pinned = revalidate_resolution(record)
    except SessionRecordError as exc:
        return (
            None,
            None,
            _resolved_send_failure(
                context,
                request,
                (
                    "RESOLUTION_UNAVAILABLE"
                    if exc.reason == "unavailable"
                    else "RESOLUTION_INVALID"
                ),
                (
                    "Contract resolution is unavailable in this session."
                    if exc.reason == "unavailable"
                    else "Stored contract resolution is invalid."
                ),
            ),
        )
    except ResolutionFailure as exc:
        return (
            None,
            None,
            _resolved_send_failure(
                context, request, exc.code, exc.message, exc.details
            ),
        )
    try:
        resolved_function = function_by_signature(
            record, request.call_context.function_signature
        )
    except ResolutionFailure as exc:
        return (
            None,
            None,
            _resolved_send_failure(
                context, request, exc.code, exc.message, exc.details
            ),
        )
    if (
        not _resolution_matches_preparation(record, request)
        or resolved_function != request.call_context.function_abi
    ):
        return (
            None,
            None,
            _resolved_send_failure(
                context,
                request,
                "STALE_RESOLUTION",
                "Resolved contract facts no longer match the approved preparation.",
            ),
        )
    return record, pinned, None


def _load_validated_send_signer(
    request: Any, context: Any, config: Any
) -> tuple[Any | None, str, dict[str, Any], dict[str, Any] | None]:
    from web3 import Web3

    transaction = request.transaction.model_dump(mode="json")
    try:
        account = _load_resolved_account(config, context)
    except (KeyError, OSError, TypeError, ValueError):
        return (
            None,
            "",
            transaction,
            _resolved_send_failure(
                context,
                request,
                "SIGNER_UNAVAILABLE",
                "Configured blockchain signer is unavailable.",
            ),
        )
    sender = Web3.to_checksum_address(account.address)
    if int(transaction["max_total_fee_wei"]) > int(config.max_total_fee_wei):
        return (
            None,
            sender,
            transaction,
            _resolved_send_failure(
                context,
                request,
                "FEE_CAP_EXCEEDED",
                "Transaction fee exceeds the configured cap.",
                {
                    "max_total_fee_wei": transaction["max_total_fee_wei"],
                    "configured_max_total_fee_wei": str(config.max_total_fee_wei),
                },
            ),
        )
    if sender != request.signer_address or sender != transaction["from_address"]:
        return (
            None,
            sender,
            transaction,
            _resolved_send_failure(
                context,
                request,
                "SIGNER_MISMATCH",
                "Prepared sender does not match the configured signer.",
                {
                    "expected_from_address": request.signer_address,
                    "observed_from_address": sender,
                },
            ),
        )
    return account, sender, transaction, None


def _transaction_fee_is_stale(
    record: Mapping[str, Any],
    transaction: Mapping[str, Any],
    fee_block: Mapping[str, Any],
) -> bool:
    base_fee_value = fee_block.get("baseFeePerGas")
    if transaction["transaction_type"] == "eip1559":
        if base_fee_value is None:
            return True
        base_fee = _quantity(base_fee_value, "baseFeePerGas")
        priority_fee = _quantity(
            rpc_call(record, "eth_maxPriorityFeePerGas", []),
            "eth_maxPriorityFeePerGas",
        )
        return int(transaction["max_priority_fee_per_gas_wei"]) < priority_fee or int(
            transaction["max_fee_per_gas_wei"]
        ) < (base_fee + priority_fee)
    live_gas_price = _quantity(rpc_call(record, "eth_gasPrice", []), "eth_gasPrice")
    return (
        base_fee_value is not None or int(transaction["gas_price_wei"]) < live_gas_price
    )


def _live_send_state_error(
    request: Any,
    context: Any,
    record: Mapping[str, Any],
    pinned: Mapping[str, Any],
    sender: str,
    transaction: Mapping[str, Any],
) -> dict[str, Any] | None:
    block_number = int(pinned["block_number"])
    block_tag = hex(block_number)
    try:
        balance = _quantity(
            rpc_call(record, "eth_getBalance", [sender, "latest"]),
            "eth_getBalance",
        )
        nonce = _quantity(
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
        required_balance = int(transaction["value_wei"]) + int(
            transaction["max_total_fee_wei"]
        )
        if balance < required_balance:
            return _resolved_send_failure(
                context,
                request,
                "INSUFFICIENT_FUNDS",
                "Signer balance cannot cover transaction value and maximum fee.",
                {
                    "balance_wei": str(balance),
                    "required_balance_wei": str(required_balance),
                },
            )
        fee_block = rpc_call(record, "eth_getBlockByNumber", [block_tag, False])
        fee_number, fee_hash = _block(fee_block, "eth_getBlockByNumber:fees")
        if fee_number != block_number or fee_hash != pinned["block_hash"]:
            raise ResolutionFailure(
                "STALE_BLOCK", "Pinned block changed during transaction submission."
            )
        if _transaction_fee_is_stale(record, transaction, fee_block):
            return _resolved_send_failure(
                context,
                request,
                "STALE_PREPARATION",
                "Prepared transaction fee no longer matches chain conditions.",
                {"fields": ["fee"]},
            )
        simulated = _hex_data(
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
        _recheck_pinned_block(record, block_number, str(pinned["block_hash"]))
    except ResolutionFailure as exc:
        return _resolved_send_failure(
            context, request, exc.code, exc.message, exc.details
        )
    return None


def _new_operation(
    request: Any, transaction_hash: str, confirmation_depth: int
) -> dict[str, Any]:
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
        "confirmation_depth": confirmation_depth,
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
    return operation


def _claim_operation_error(
    operation: Mapping[str, Any], request: Any, context: Any
) -> dict[str, Any] | None:
    try:
        claimed = claim_operation_record(
            operation,
            context,
            validator=validate_operation_record,
            digester=operation_digest,
        )
    except (OSError, SessionRecordError) as exc:
        reason = exc.reason if isinstance(exc, SessionRecordError) else "unavailable"
        return _resolved_send_failure(
            context,
            request,
            "OPERATION_UNAVAILABLE",
            "Submission operation could not be stored.",
            {"reason": reason, "broadcast_attempted": False},
        )
    if claimed:
        return None
    return _resolved_send_failure(
        context,
        request,
        "OPERATION_INVALID",
        "This preparation already has a submission operation.",
        {"broadcast_attempted": False},
    )


def _replace_submitted_operation(
    operation: Mapping[str, Any],
    expected_operation: Mapping[str, Any],
    request: Any,
    context: Any,
    transaction: Mapping[str, Any],
    transaction_hash: str,
) -> dict[str, Any] | None:
    try:
        replaced = replace_operation_record(
            operation,
            expected_operation,
            context,
            validator=validate_operation_record,
            digester=operation_digest,
        )
        if not replaced:
            current = load_operation_record(
                request.preparation_digest,
                session_id=str(getattr(context, "session_id", "") or ""),
                env=getattr(context, "env", None),
                validator=validate_operation_record,
                digester=operation_digest,
            )
            return _send_terminal(
                context,
                transaction,
                request.preparation_digest,
                str(current["state"]),
                {"ok": True},
                transaction_hash=transaction_hash,
                broadcast_attempts=1,
            )
    except (OSError, SessionRecordError) as exc:
        reason = exc.reason if isinstance(exc, SessionRecordError) else "unavailable"
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "broadcast_unknown",
            _error(
                "OPERATION_UNAVAILABLE",
                "Transaction was submitted but its operation status could not be stored.",
                {"reason": reason, "transaction_hash": transaction_hash},
            ),
            transaction_hash=transaction_hash,
            broadcast_attempts=1,
        )
    return None


def _broadcast_claimed_operation(
    operation: dict[str, Any],
    request: Any,
    context: Any,
    record: Mapping[str, Any],
    transaction: Mapping[str, Any],
    raw_transaction: Any,
    transaction_hash: str,
) -> dict[str, Any]:
    expected_operation = dict(operation)
    try:
        submitted_hash = _hex_data(
            rpc_call(
                record, "eth_sendRawTransaction", [web3_hex_data(raw_transaction)]
            ),
            "eth_sendRawTransaction",
        )
    except ResolutionFailure:
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
    if submitted_hash != transaction_hash:
        operation["last_error_code"] = "OPERATION_INVALID"
        storage_error = _replace_submitted_operation(
            operation,
            expected_operation,
            request,
            context,
            transaction,
            transaction_hash,
        )
        if storage_error is not None:
            return storage_error
        return _send_terminal(
            context,
            transaction,
            request.preparation_digest,
            "broadcast_unknown",
            _error(
                "OPERATION_INVALID",
                "RPC returned a transaction hash different from the signed transaction.",
                {"transaction_hash": transaction_hash},
            ),
            transaction_hash=transaction_hash,
            broadcast_attempts=1,
        )
    operation["state"] = "pending"
    storage_error = _replace_submitted_operation(
        operation,
        expected_operation,
        request,
        context,
        transaction,
        transaction_hash,
    )
    if storage_error is not None:
        return storage_error
    return _send_terminal(
        context,
        transaction,
        request.preparation_digest,
        "pending",
        {"ok": True},
        transaction_hash=transaction_hash,
        broadcast_attempts=1,
    )


def send_resolved_transaction(
    request: Any,
    context: Any,
    config: Any,
) -> dict[str, Any]:
    from web3 import Web3

    record, pinned, error = _load_validated_send_resolution(request, context)
    if error is not None:
        return error
    assert record is not None and pinned is not None
    account, sender, transaction, error = _load_validated_send_signer(
        request, context, config
    )
    if error is not None:
        return error
    assert account is not None
    live_error = _live_send_state_error(
        request, context, record, pinned, sender, transaction
    )
    if live_error is not None:
        return live_error
    signed = account.sign_transaction(
        {
            key: value
            for key, value in _rpc_transaction(transaction).items()
            if key != "from"
        }
    )
    raw_transaction = signed.raw_transaction
    transaction_hash = web3_hex_data(Web3.keccak(raw_transaction)).lower()
    operation = _new_operation(
        request, transaction_hash, int(config.confirmation_depth)
    )
    claim_error = _claim_operation_error(operation, request, context)
    if claim_error is not None:
        return claim_error
    return _broadcast_claimed_operation(
        operation,
        request,
        context,
        record,
        transaction,
        raw_transaction,
        transaction_hash,
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
    result = {
        "ok": True,
        "state": "succeeded",
        "action": "operation_status",
        "data": data,
    }
    encoded = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    if len(encoded) > MAX_OPERATION_RECORD_BYTES:
        return _error(
            "RESULT_TOO_LARGE",
            "Blockchain operation status exceeds the result limit.",
        )
    return result


def _persist_operation(
    operation: Mapping[str, Any],
    expected_operation: Mapping[str, Any],
    context: Any,
) -> dict[str, Any]:
    try:
        replaced = replace_operation_record(
            operation,
            expected_operation,
            context,
            validator=validate_operation_record,
            digester=operation_digest,
        )
        if not replaced:
            operation = load_operation_record(
                str(operation["preparation_digest"]),
                session_id=str(getattr(context, "session_id", "") or ""),
                env=getattr(context, "env", None),
                validator=validate_operation_record,
                digester=operation_digest,
            )
    except (OSError, SessionRecordError) as exc:
        reason = exc.reason if isinstance(exc, SessionRecordError) else "unavailable"
        return _error(
            "RESULT_TOO_LARGE" if reason == "size_limit" else "OPERATION_UNAVAILABLE",
            (
                "Blockchain operation status exceeds the result limit."
                if reason == "size_limit"
                else "Blockchain operation status could not be stored."
            ),
            {"reason": reason},
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
            function = function_by_signature(record, postcondition.function_signature)
            call_data = encode_function_call(Web3(), function, postcondition.arguments)
            raw_result = _hex_data(
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
        except DecodingError:
            error_code = "ABI_DECODE_FAILED"
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


def _load_operation_status_facts(
    preparation_digest_value: str, context: Any
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    try:
        operation = load_operation_record(
            preparation_digest_value,
            session_id=str(getattr(context, "session_id", "") or ""),
            env=getattr(context, "env", None),
            validator=validate_operation_record,
            digester=operation_digest,
        )
    except SessionRecordError as exc:
        code = {
            "unavailable": "OPERATION_UNAVAILABLE",
            "size_limit": "RESULT_TOO_LARGE",
        }.get(exc.reason, "OPERATION_INVALID")
        return (
            None,
            None,
            None,
            _error(
                code,
                (
                    "Blockchain operation is unavailable in this session."
                    if exc.reason == "unavailable"
                    else (
                        "Blockchain operation status exceeds the result limit."
                        if exc.reason == "size_limit"
                        else "Stored blockchain operation is invalid."
                    )
                ),
            ),
        )
    try:
        preparation = _load_resolved_preparation(preparation_digest_value, context)
    except SessionRecordError:
        return (
            None,
            None,
            None,
            _error(
                "OPERATION_INVALID",
                "Stored operation preparation is unavailable.",
            ),
        )
    prepared_model = RESOLVED_PREPARATION_ADAPTER.validate_python(preparation)
    try:
        record = load_resolution(operation["resolution_digest"], context)
        pinned = revalidate_chain_identity(record)
    except SessionRecordError as exc:
        return (
            None,
            None,
            None,
            resolution_record_error_result(exc),
        )
    except ResolutionFailure as exc:
        return None, None, None, resolution_error_result(exc)
    if preparation["resolution_digest"] != operation["resolution_digest"]:
        return (
            None,
            None,
            None,
            _error(
                "OPERATION_INVALID",
                "Stored operation does not match its preparation.",
            ),
        )
    if not _resolution_matches_preparation(record, prepared_model):
        return (
            None,
            None,
            None,
            _error(
                "STALE_RESOLUTION",
                "Resolved contract facts no longer match the operation.",
            ),
        )
    return operation, record, pinned, None


def _observe_operation_receipt(
    operation: Mapping[str, Any], record: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    receipt = rpc_call(
        record,
        "eth_getTransactionReceipt",
        [operation["transaction_hash"]],
    )
    if receipt is None:
        return None, None
    if not isinstance(receipt, Mapping):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid receipt.",
            {"operation": "eth_getTransactionReceipt"},
        )
    returned_hash = receipt.get("transactionHash")
    if (
        not isinstance(returned_hash, str)
        or returned_hash.lower() != operation["transaction_hash"]
    ):
        return None, _error(
            "OPERATION_INVALID",
            "Receipt transaction hash does not match the stored operation.",
        )
    receipt_number = _quantity(receipt.get("blockNumber"), "receipt:blockNumber")
    receipt_hash = _hex_data(
        receipt.get("blockHash"), "receipt:blockHash", exact_bytes=32
    )
    receipt_status = _quantity(receipt.get("status"), "receipt:status")
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
        canonical_number, canonical_hash = _block(
            canonical_block,
            "eth_getBlockByNumber:receipt",
        )
    return {
        "receipt": receipt,
        "number": receipt_number,
        "hash": receipt_hash,
        "status": receipt_status,
        "canonical_number": canonical_number,
        "canonical_hash": canonical_hash,
    }, None


def _persist_missing_receipt(
    operation: dict[str, Any], expected_operation: Mapping[str, Any], context: Any
) -> dict[str, Any]:
    if operation["receipt_block_number"] is not None:
        operation["state"] = "reorged"
        operation["last_error_code"] = None
    elif operation["state"] != "broadcast_unknown":
        operation["state"] = "pending"
        operation["last_error_code"] = None
    return _persist_operation(operation, expected_operation, context)


def _receipt_is_reorged(
    operation: Mapping[str, Any], observation: Mapping[str, Any], head_number: int
) -> bool:
    prior_number = operation["receipt_block_number"]
    prior_hash = operation["receipt_block_hash"]
    return (
        observation["number"] > head_number
        or observation["canonical_number"] != observation["number"]
        or observation["canonical_hash"] != observation["hash"]
        or (prior_number is not None and int(prior_number) != observation["number"])
        or (prior_hash is not None and prior_hash != observation["hash"])
    )


def _update_confirming_operation(
    operation: dict[str, Any], observation: Mapping[str, Any], head_number: int
) -> None:
    receipt = observation["receipt"]
    gas_used = (
        str(_quantity(receipt["gasUsed"], "receipt:gasUsed"))
        if receipt.get("gasUsed") is not None
        else None
    )
    effective_gas_price = (
        str(
            _quantity(
                receipt["effectiveGasPrice"],
                "receipt:effectiveGasPrice",
            )
        )
        if receipt.get("effectiveGasPrice") is not None
        else None
    )
    operation.update(
        {
            "state": "confirming",
            "receipt_block_number": str(observation["number"]),
            "receipt_block_hash": observation["hash"],
            "receipt_status": observation["status"],
            "confirmations": head_number - observation["number"] + 1,
            "gas_used": gas_used,
            "effective_gas_price_wei": effective_gas_price,
            "postcondition_results": [],
            "last_error_code": None,
        }
    )


def _finalize_operation(
    operation: dict[str, Any],
    expected_operation: Mapping[str, Any],
    record: Mapping[str, Any],
    pinned: Mapping[str, Any],
    context: Any,
) -> dict[str, Any]:
    if operation["receipt_status"] == 0:
        terminal_state = "reverted"
    else:
        results = _postcondition_results(
            operation, record, int(operation["receipt_block_number"])
        )
        operation["postcondition_results"] = results
        observation_error = next(
            (result["error_code"] for result in results if result["error_code"]),
            None,
        )
        if observation_error is not None:
            operation["state"] = "confirming"
            operation["last_error_code"] = observation_error
            return _persist_operation(operation, expected_operation, context)
        terminal_state = (
            "succeeded"
            if all(result["matched"] for result in results)
            else "postcondition_failed"
        )
    try:
        _recheck_pinned_block(
            record, int(pinned["block_number"]), str(pinned["block_hash"])
        )
    except ResolutionFailure:
        operation["state"] = "confirming"
        operation["last_error_code"] = "STALE_BLOCK"
        operation["postcondition_results"] = []
        return _persist_operation(operation, expected_operation, context)
    operation["state"] = terminal_state
    operation["last_error_code"] = (
        "POSTCONDITION_FAILED" if terminal_state == "postcondition_failed" else None
    )
    return _persist_operation(operation, expected_operation, context)


def operation_status(preparation_digest_value: str, context: Any) -> dict[str, Any]:
    operation, record, pinned, error = _load_operation_status_facts(
        preparation_digest_value, context
    )
    if error is not None:
        return error
    assert operation is not None and record is not None and pinned is not None
    if operation["state"] in {
        "succeeded",
        "reverted",
        "reorged",
        "postcondition_failed",
    }:
        return _operation_result(operation)
    expected_operation = dict(operation)
    head_number = int(pinned["block_number"])
    try:
        observation, error = _observe_operation_receipt(operation, record)
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    if error is not None:
        return error
    if observation is None:
        return _persist_missing_receipt(operation, expected_operation, context)
    if _receipt_is_reorged(operation, observation, head_number):
        operation["state"] = "reorged"
        operation["last_error_code"] = None
        return _persist_operation(operation, expected_operation, context)
    try:
        _update_confirming_operation(operation, observation, head_number)
    except ResolutionFailure as exc:
        return resolution_error_result(exc)
    if operation["confirmations"] < int(operation["confirmation_depth"]):
        return _persist_operation(operation, expected_operation, context)
    return _finalize_operation(operation, expected_operation, record, pinned, context)
