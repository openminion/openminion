from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
from typing import Any, Mapping, cast

from pydantic import ValidationError

from openminion.modules.tool.plugin_api import (
    BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID_MESSAGE,
    BlockchainCallPreview,
    BlockchainSendConfirmationPreview,
)

from .runtime import preparation_digest
from .abi import abi_signature, encode_function_call, normalize_abi_values
from .transaction_schemas import SEND_REQUEST_ADAPTER

MAX_APPROVAL_CALLDATA_BYTES = 4096
MAX_APPROVAL_PREVIEW_BYTES = 16384
PREVIEW_INVALID_CODE = "BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID"
PREVIEW_INVALID_MESSAGE = BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID_MESSAGE
_PREVIEW_FIELDS = {
    "schema_version",
    "chain_id",
    "from_address",
    "to_address",
    "value_wei",
    "transaction_type",
    "nonce",
    "gas_limit",
    "gas_price_wei",
    "max_fee_per_gas_wei",
    "max_priority_fee_per_gas_wei",
    "max_total_fee_wei",
    "calldata_bytes",
    "calldata_sha256",
    "calldata_hex",
    "preparation_digest",
    "call",
    "opaque_calldata",
}
_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^0x(?:[0-9a-f]{2})*$")


class BlockchainConfirmationPreviewError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def preview_to_dict(
    preview: BlockchainSendConfirmationPreview,
) -> dict[str, Any]:
    return asdict(preview)


def canonical_blockchain_send_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """Return the validated JSON form used by approval and execution."""
    return SEND_REQUEST_ADAPTER.validate_python(dict(args)).model_dump(mode="json")


def _serialized_size(value: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    )


def build_blockchain_send_confirmation_preview(
    args: Mapping[str, Any],
) -> BlockchainSendConfirmationPreview:
    from web3 import Web3

    try:
        request = SEND_REQUEST_ADAPTER.validate_python(dict(args))
    except ValidationError as exc:
        raise BlockchainConfirmationPreviewError("request_schema") from exc

    transaction = request.transaction.model_dump(mode="json")
    call_context = (
        request.call_context.model_dump(mode="json")
        if request.call_context is not None
        else None
    )
    if preparation_digest(transaction, call_context) != request.preparation_digest:
        raise BlockchainConfirmationPreviewError("preparation_digest")

    data = transaction["data"].lower()
    calldata = bytes.fromhex(data[2:])
    if len(calldata) > MAX_APPROVAL_CALLDATA_BYTES:
        raise BlockchainConfirmationPreviewError("calldata_limit")

    call: BlockchainCallPreview | None = None
    if request.call_context is not None:
        expected_signature = request.call_context.function_signature
        if expected_signature != abi_signature(request.call_context.function_abi):
            raise BlockchainConfirmationPreviewError("call_context")
        try:
            encoded = encode_function_call(
                Web3(),
                request.call_context.function_abi,
                request.call_context.function_args,
            )
        except (TypeError, ValueError) as exc:
            raise BlockchainConfirmationPreviewError("call_context") from exc
        if encoded.lower() != data:
            raise BlockchainConfirmationPreviewError("call_context")
        call = BlockchainCallPreview(
            function_signature=expected_signature,
            function_args=normalize_abi_values(
                request.call_context.function_args,
                request.call_context.function_abi.inputs,
                Web3(),
            ),
        )

    gas_limit = int(transaction["gas_limit"])
    gas_price = transaction.get("gas_price_wei")
    max_fee = transaction.get("max_fee_per_gas_wei")
    fee_per_gas = cast(str, gas_price if gas_price is not None else max_fee)
    expected_max_total = gas_limit * int(fee_per_gas)
    if expected_max_total != int(transaction["max_total_fee_wei"]):
        raise BlockchainConfirmationPreviewError("request_schema")

    opaque = bool(calldata) and call is None
    preview = BlockchainSendConfirmationPreview(
        schema_version="blockchain-send-preview-v1",
        chain_id=str(transaction["chain_id"]),
        from_address=Web3.to_checksum_address(transaction["from_address"]),
        to_address=Web3.to_checksum_address(transaction["to_address"]),
        value_wei=transaction["value_wei"],
        transaction_type=transaction["transaction_type"],
        nonce=transaction["nonce"],
        gas_limit=transaction["gas_limit"],
        gas_price_wei=gas_price,
        max_fee_per_gas_wei=max_fee,
        max_priority_fee_per_gas_wei=transaction.get("max_priority_fee_per_gas_wei"),
        max_total_fee_wei=transaction["max_total_fee_wei"],
        calldata_bytes=str(len(calldata)),
        calldata_sha256=hashlib.sha256(calldata).hexdigest(),
        calldata_hex=data if opaque else None,
        preparation_digest=request.preparation_digest,
        call=call,
        opaque_calldata=opaque,
    )
    if _serialized_size(preview_to_dict(preview)) > MAX_APPROVAL_PREVIEW_BYTES:
        raise BlockchainConfirmationPreviewError("preview_limit")
    return preview


def _call_preview(value: Any) -> BlockchainCallPreview | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "function_signature",
        "function_args",
    }:
        raise BlockchainConfirmationPreviewError("request_schema")
    if not isinstance(value.get("function_signature"), str) or not isinstance(
        value.get("function_args"), list
    ):
        raise BlockchainConfirmationPreviewError("request_schema")
    return BlockchainCallPreview(
        function_signature=value["function_signature"],
        function_args=value["function_args"],
    )


def _preview_from_mapping(
    value: Mapping[str, Any], call: BlockchainCallPreview | None
) -> BlockchainSendConfirmationPreview:
    return BlockchainSendConfirmationPreview(
        schema_version=value.get("schema_version"),
        chain_id=value.get("chain_id"),
        from_address=value.get("from_address"),
        to_address=value.get("to_address"),
        value_wei=value.get("value_wei"),
        transaction_type=value.get("transaction_type"),
        nonce=value.get("nonce"),
        gas_limit=value.get("gas_limit"),
        gas_price_wei=value.get("gas_price_wei"),
        max_fee_per_gas_wei=value.get("max_fee_per_gas_wei"),
        max_priority_fee_per_gas_wei=value.get("max_priority_fee_per_gas_wei"),
        max_total_fee_wei=value.get("max_total_fee_wei"),
        calldata_bytes=value.get("calldata_bytes"),
        calldata_sha256=value.get("calldata_sha256"),
        calldata_hex=value.get("calldata_hex"),
        preparation_digest=value.get("preparation_digest"),
        call=call,
        opaque_calldata=value.get("opaque_calldata"),
    )


def parse_blockchain_send_confirmation_preview(
    value: Mapping[str, Any],
) -> BlockchainSendConfirmationPreview:
    from web3 import Web3

    if set(value) != _PREVIEW_FIELDS:
        raise BlockchainConfirmationPreviewError("request_schema")
    preview = _preview_from_mapping(value, _call_preview(value.get("call")))
    if (
        preview.schema_version != "blockchain-send-preview-v1"
        or not all(
            isinstance(item, str)
            for item in (
                preview.chain_id,
                preview.from_address,
                preview.to_address,
                preview.value_wei,
                preview.transaction_type,
                preview.nonce,
                preview.gas_limit,
                preview.max_total_fee_wei,
                preview.calldata_bytes,
                preview.calldata_sha256,
                preview.preparation_digest,
            )
        )
        or not isinstance(preview.opaque_calldata, bool)
    ):
        raise BlockchainConfirmationPreviewError("request_schema")
    decimal_values = (
        preview.chain_id,
        preview.value_wei,
        preview.nonce,
        preview.gas_limit,
        preview.max_total_fee_wei,
        preview.calldata_bytes,
    )
    optional_decimal_values = (
        preview.gas_price_wei,
        preview.max_fee_per_gas_wei,
        preview.max_priority_fee_per_gas_wei,
    )
    if (
        not all(_DECIMAL_RE.fullmatch(item) for item in decimal_values)
        or not all(
            item is None
            or (isinstance(item, str) and _DECIMAL_RE.fullmatch(item) is not None)
            for item in optional_decimal_values
        )
        or _ADDRESS_RE.fullmatch(preview.from_address) is None
        or _ADDRESS_RE.fullmatch(preview.to_address) is None
        or not Web3.is_checksum_address(preview.from_address)
        or not Web3.is_checksum_address(preview.to_address)
        or _HASH_RE.fullmatch(preview.calldata_sha256) is None
        or _DIGEST_RE.fullmatch(preview.preparation_digest) is None
    ):
        raise BlockchainConfirmationPreviewError("request_schema")
    if preview.transaction_type == "legacy":
        if preview.gas_price_wei is None or any(
            item is not None
            for item in (
                preview.max_fee_per_gas_wei,
                preview.max_priority_fee_per_gas_wei,
            )
        ):
            raise BlockchainConfirmationPreviewError("request_schema")
        fee_per_gas = preview.gas_price_wei
    elif preview.transaction_type == "eip1559":
        if (
            preview.gas_price_wei is not None
            or preview.max_fee_per_gas_wei is None
            or preview.max_priority_fee_per_gas_wei is None
        ):
            raise BlockchainConfirmationPreviewError("request_schema")
        fee_per_gas = preview.max_fee_per_gas_wei
    else:
        raise BlockchainConfirmationPreviewError("request_schema")
    if int(preview.gas_limit) * int(fee_per_gas) != int(preview.max_total_fee_wei):
        raise BlockchainConfirmationPreviewError("request_schema")
    if preview.call is not None:
        if preview.opaque_calldata or preview.calldata_hex is not None:
            raise BlockchainConfirmationPreviewError("request_schema")
    elif preview.opaque_calldata:
        if (
            not isinstance(preview.calldata_hex, str)
            or _HEX_RE.fullmatch(preview.calldata_hex) is None
            or len(bytes.fromhex(preview.calldata_hex[2:]))
            != int(preview.calldata_bytes)
            or hashlib.sha256(bytes.fromhex(preview.calldata_hex[2:])).hexdigest()
            != preview.calldata_sha256
        ):
            raise BlockchainConfirmationPreviewError("request_schema")
    elif preview.calldata_hex is not None or preview.calldata_bytes != "0":
        raise BlockchainConfirmationPreviewError("request_schema")
    if int(preview.calldata_bytes) > MAX_APPROVAL_CALLDATA_BYTES:
        raise BlockchainConfirmationPreviewError("calldata_limit")
    if _serialized_size(preview_to_dict(preview)) > MAX_APPROVAL_PREVIEW_BYTES:
        raise BlockchainConfirmationPreviewError("preview_limit")
    return preview
