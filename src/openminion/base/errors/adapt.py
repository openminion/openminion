"""Normalize exceptions and mappings into ErrorInfo dictionaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import ErrorInfo

_INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
_INTERNAL_ERROR_MESSAGE = "Internal error"


def error_info_from_mapping(
    payload: Mapping[str, Any],
    *,
    default_code: str = _INTERNAL_ERROR_CODE,
    default_message: str = _INTERNAL_ERROR_MESSAGE,
    namespace: str | None = None,
) -> ErrorInfo:
    error_payload = _nested_error_mapping(payload)
    code = _normalized_string(error_payload.get("code")) or default_code
    message = _normalized_string(error_payload.get("message")) or default_message
    details = error_payload.get("details")
    if details is None and "detail" in error_payload:
        details = error_payload.get("detail")
    return ErrorInfo(
        code=code,
        message=message,
        details=_normalized_details(details),
        namespace=namespace or _normalized_string(error_payload.get("namespace")),
    )


def error_info_from_exception(
    error: BaseException,
    *,
    default_code: str = _INTERNAL_ERROR_CODE,
    default_message: str = _INTERNAL_ERROR_MESSAGE,
    namespace: str | None = None,
) -> ErrorInfo:
    payload = _mapping_from_exception(error)
    if payload is not None:
        return error_info_from_mapping(
            payload,
            default_code=default_code,
            default_message=default_message,
            namespace=namespace or _namespace_from_exception(error),
        )

    code = _normalized_string(getattr(error, "code", None)) or default_code
    message = _normalized_string(getattr(error, "message", None)) or str(error).strip()
    if not message:
        message = default_message
    details = getattr(error, "details", None)
    if details is None and hasattr(error, "detail"):
        details = getattr(error, "detail")
    return ErrorInfo(
        code=code,
        message=message,
        details=_normalized_details(details),
        namespace=namespace or _namespace_from_exception(error),
    )


def error_dict_from_mapping(
    payload: Mapping[str, Any],
    *,
    default_code: str = _INTERNAL_ERROR_CODE,
    default_message: str = _INTERNAL_ERROR_MESSAGE,
    include_details: bool = True,
    include_empty_details: bool = True,
    include_namespace: bool = False,
    namespace: str | None = None,
) -> dict[str, Any]:
    info = error_info_from_mapping(
        payload,
        default_code=default_code,
        default_message=default_message,
        namespace=namespace,
    )
    return info.to_dict(
        include_details=include_details,
        include_empty_details=include_empty_details,
        include_namespace=include_namespace,
    )


def error_dict_from_exception(
    error: BaseException,
    *,
    default_code: str = _INTERNAL_ERROR_CODE,
    default_message: str = _INTERNAL_ERROR_MESSAGE,
    include_details: bool = True,
    include_empty_details: bool = True,
    include_namespace: bool = False,
    namespace: str | None = None,
) -> dict[str, Any]:
    info = error_info_from_exception(
        error,
        default_code=default_code,
        default_message=default_message,
        namespace=namespace,
    )
    return info.to_dict(
        include_details=include_details,
        include_empty_details=include_empty_details,
        include_namespace=include_namespace,
    )


def _mapping_from_exception(error: BaseException) -> Mapping[str, Any] | None:
    to_dict = getattr(error, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return payload
    return None


def _nested_error_mapping(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("error")
    if isinstance(nested, Mapping) and (
        "code" in nested
        or "message" in nested
        or "detail" in nested
        or "details" in nested
    ):
        return nested
    return payload


def _normalized_details(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {} if value is None else {"value": value}


def _normalized_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _namespace_from_exception(error: BaseException) -> str | None:
    module_name = type(error).__module__
    for marker in (".modules.", ".services.", ".tools.", ".api.", ".base."):
        if marker in module_name:
            remainder = module_name.split(marker, 1)[1]
            namespace = remainder.split(".", 1)[0].strip()
            if namespace:
                return namespace
    return None
