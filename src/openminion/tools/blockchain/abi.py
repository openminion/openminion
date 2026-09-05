from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any, cast

from eth_abi.exceptions import DecodingError

from .schema_types import AbiParameter, ErrorAbi, EventAbi, FunctionAbi

_OUTER_ARRAY_RE = re.compile(r"^(.*)(\[(?:0|[1-9][0-9]*)?\])$")


def canonical_abi_type(parameter: AbiParameter) -> str:
    array_start = parameter.type.find("[")
    base_type = parameter.type if array_start < 0 else parameter.type[:array_start]
    suffix = "" if array_start < 0 else parameter.type[array_start:]
    if base_type != "tuple":
        return f"{base_type}{suffix}"
    components = parameter.components or []
    body = ",".join(canonical_abi_type(component) for component in components)
    return f"({body}){suffix}"


def abi_signature(
    abi: FunctionAbi | ErrorAbi | EventAbi,
) -> str:
    inputs = ",".join(canonical_abi_type(parameter) for parameter in abi.inputs)
    return f"{abi.name}({inputs})"


def abi_selector(abi: FunctionAbi | ErrorAbi, web3: Any) -> str:
    value = web3.keccak(text=abi_signature(abi))[:4].hex()
    return value if value.startswith("0x") else f"0x{value}"


def event_topic(abi: EventAbi, web3: Any) -> str:
    value = web3.keccak(text=abi_signature(abi)).hex()
    return value if value.startswith("0x") else f"0x{value}"


def _item_parameter(parameter: AbiParameter) -> AbiParameter:
    match = _OUTER_ARRAY_RE.fullmatch(parameter.type)
    if match is None:
        raise ValueError("ABI value is not an array")
    return AbiParameter(
        name=parameter.name,
        type=match.group(1),
        components=parameter.components,
    )


def validate_abi_value(value: Any, parameter: AbiParameter) -> None:
    array_match = _OUTER_ARRAY_RE.fullmatch(parameter.type)
    if array_match:
        if not isinstance(value, (list, tuple)):
            raise ValueError("ABI array value must be ordered")
        length = array_match.group(2)[1:-1]
        if length and len(value) != int(length):
            raise ValueError("ABI fixed array value has the wrong length")
        item = _item_parameter(parameter)
        for member in value:
            validate_abi_value(member, item)
        return
    if parameter.type == "tuple":
        if not isinstance(value, (list, tuple)) or len(value) != len(
            parameter.components or []
        ):
            raise ValueError("ABI tuple value must match component order")
        for member, component in zip(value, parameter.components or [], strict=True):
            validate_abi_value(member, component)
        return
    valid = False
    if parameter.type == "address":
        valid = (
            isinstance(value, str)
            and re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is not None
        )
    elif parameter.type == "bool":
        valid = isinstance(value, bool)
    elif parameter.type == "string":
        valid = isinstance(value, str)
    elif parameter.type == "bytes" or parameter.type.startswith("bytes"):
        valid = isinstance(value, (bytes, bytearray)) or (
            isinstance(value, str)
            and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value) is not None
        )
    elif parameter.type.startswith("uint") or parameter.type.startswith("int"):
        valid = isinstance(value, int) and not isinstance(value, bool)
    else:
        valid = False
    if not valid:
        raise ValueError("ABI value does not match its declared type")


def validate_abi_values(
    values: Sequence[Any], parameters: Sequence[AbiParameter]
) -> None:
    if len(values) != len(parameters):
        raise ValueError("ABI argument count does not match")
    for value, parameter in zip(values, parameters, strict=True):
        validate_abi_value(value, parameter)


def normalize_abi_value(value: Any, parameter: AbiParameter, web3: Any) -> Any:
    if _OUTER_ARRAY_RE.fullmatch(parameter.type):
        item = _item_parameter(parameter)
        return [normalize_abi_value(member, item, web3) for member in value]
    if parameter.type == "tuple":
        return [
            normalize_abi_value(member, component, web3)
            for member, component in zip(value, parameter.components or [], strict=True)
        ]
    if parameter.type == "address":
        return web3.to_checksum_address(value)
    if parameter.type == "bytes" or parameter.type.startswith("bytes"):
        token = value if isinstance(value, str) else value.hex()
        return token.lower() if token.startswith("0x") else f"0x{token.lower()}"
    if parameter.type.startswith("uint") or parameter.type.startswith("int"):
        return str(int(value))
    return value


def normalize_abi_values(
    values: Sequence[Any], parameters: Sequence[AbiParameter], web3: Any
) -> list[Any]:
    return [
        normalize_abi_value(value, parameter, web3)
        for value, parameter in zip(values, parameters, strict=True)
    ]


def encode_function_call(web3: Any, abi: FunctionAbi, values: Sequence[Any]) -> str:
    validate_abi_values(values, abi.inputs)
    encoded = web3.eth.contract(abi=[abi.model_dump(mode="json")]).encode_abi(
        abi.name,
        args=list(values),
    )
    return cast(str, encoded.lower())


def decode_abi_values(
    web3: Any,
    parameters: Sequence[AbiParameter],
    data: bytes,
) -> list[Any]:
    types = [canonical_abi_type(parameter) for parameter in parameters]
    decoded = web3.codec.decode(types, data)
    return normalize_abi_values(decoded, parameters, web3)


def revert_data_from_exception(error: BaseException) -> str | None:
    from web3.exceptions import ContractLogicError

    if not isinstance(error, ContractLogicError):
        return None
    data = getattr(error, "data", None)
    if (
        not isinstance(data, str)
        or re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", data) is None
    ):
        return None
    return data.lower()


def decode_revert_fact(
    web3: Any,
    data: str | None,
    error_abis: Sequence[ErrorAbi] = (),
) -> dict[str, Any]:
    if data is None:
        return {"kind": "data_unavailable", "raw_data": None}
    raw = data.lower()
    payload = bytes.fromhex(raw[2:])
    selector = payload[:4]
    if selector == bytes.fromhex("08c379a0"):
        try:
            reason = web3.codec.decode(["string"], payload[4:])[0]
        except (DecodingError, ValueError):
            return {"kind": "unknown", "raw_data": raw}
        return {"kind": "standard_error", "reason": reason, "raw_data": raw}
    if selector == bytes.fromhex("4e487b71"):
        try:
            code = web3.codec.decode(["uint256"], payload[4:])[0]
        except (DecodingError, ValueError):
            return {"kind": "unknown", "raw_data": raw}
        return {"kind": "panic", "code": str(code), "raw_data": raw}
    for error_abi in error_abis:
        if selector.hex() != abi_selector(error_abi, web3).removeprefix("0x"):
            continue
        try:
            arguments = decode_abi_values(web3, error_abi.inputs, payload[4:])
        except (DecodingError, ValueError):
            return {"kind": "unknown", "raw_data": raw}
        return {
            "kind": "custom_error",
            "signature": abi_signature(error_abi),
            "arguments": arguments,
            "raw_data": raw,
        }
    return {"kind": "unknown", "raw_data": raw}
