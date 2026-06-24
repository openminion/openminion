"""Google Workspace tool interfaces."""

from typing import Any, Dict

GWS_INTERFACE_VERSION = "v1"

GWS_CALL_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["service", "resource_path", "method"],
    "properties": {
        "service": {"type": "string"},
        "resource_path": {"type": "array", "items": {"type": "string"}},
        "method": {"type": "string"},
        "params": {"type": "object"},
        "json": {"type": "object"},
        "dry_run": {"type": "boolean"},
        "timeout_seconds": {"type": "number", "minimum": 0},
        "expect_large_output": {"type": "boolean"},
        "force_risk": {
            "type": ["string", "null"],
            "enum": ["read", "write", "admin", None],
        },
    },
}

GWS_SCHEMA_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["method_full"],
    "properties": {
        "method_full": {"type": "string"},
        "timeout_seconds": {"type": "number", "minimum": 0},
    },
}

GWS_AUTH_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "timeout_seconds": {"type": "number", "minimum": 0},
    },
}

GWS_AUTH_EXPORT_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["output_path"],
    "properties": {
        "output_path": {"type": "string"},
        "overwrite": {"type": "boolean"},
        "timeout_seconds": {"type": "number", "minimum": 0},
    },
}

GWS_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["ok", "source", "content"],
    "properties": {
        "ok": {"type": "boolean"},
        "source": {"type": "string"},
        "content": {"type": "string"},
        "data": {},
        "data_format": {"type": "string"},
        "raw_stdout": {"type": ["string", "null"]},
        "raw_stderr": {"type": "string"},
        "error": {
            "type": ["object", "null"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
        "metrics": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": "integer"},
                "exit_code": {"type": "integer"},
                "timed_out": {"type": "boolean"},
                "stdout_bytes": {"type": "integer"},
                "stderr_bytes": {"type": "integer"},
            },
        },
        "risk": {"type": "string", "enum": ["read", "write", "admin"]},
    },
}


_GWS_REQUEST_SCHEMAS = {
    "gws.call": GWS_CALL_REQUEST_SCHEMA,
    "gws.schema": GWS_SCHEMA_REQUEST_SCHEMA,
    "gws.auth.setup": GWS_AUTH_REQUEST_SCHEMA,
    "gws.auth.login": GWS_AUTH_REQUEST_SCHEMA,
    "gws.auth.export": GWS_AUTH_EXPORT_REQUEST_SCHEMA,
}


def _json_type(value: Any) -> str:
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    if value is None:
        return "null"
    return ""


def _expected_types(expected_type: Any) -> list[str]:
    if isinstance(expected_type, str):
        return [expected_type]
    if isinstance(expected_type, list):
        return [str(item) for item in expected_type]
    return []


def _type_matches(actual_type: str, expected_types: list[str]) -> bool:
    if actual_type == "null":
        return True
    if actual_type in expected_types:
        return True
    return actual_type in {"integer", "number"} and "number" in expected_types


def _validate_array_items(value: Any, prop_def: Dict[str, Any]) -> bool:
    if _json_type(value) != "array" or "items" not in prop_def:
        return True
    items_spec = prop_def["items"]
    if "type" not in items_spec:
        return True
    item_types = _expected_types(items_spec["type"])
    return all(_type_matches(_json_type(item), item_types) for item in value)


def _validate_property_value(value: Any, prop_def: Dict[str, Any]) -> bool:
    expected = _expected_types(prop_def.get("type"))
    if not expected:
        return True
    actual_type = _json_type(value)
    if not actual_type or not _type_matches(actual_type, expected):
        return False
    if "minimum" in prop_def and isinstance(value, (int, float)):
        if value < prop_def["minimum"]:
            return False
    if "enum" in prop_def and value not in prop_def["enum"]:
        return False
    return _validate_array_items(value, prop_def)


def validate_request_schema(operation: str, request: Dict[str, Any]) -> bool:
    schema = _GWS_REQUEST_SCHEMAS.get(operation)
    if schema is None:
        return False
    if any(field not in request for field in schema.get("required", [])):
        return False
    properties = schema.get("properties", {})
    return all(
        _validate_property_value(field_value, properties[field_name])
        for field_name, field_value in request.items()
        if field_name in properties
    )


def validate_response_schema(response: Dict[str, Any]) -> bool:
    required = GWS_RESPONSE_SCHEMA.get("required", [])
    if any(field not in response for field in required):
        return False
    properties = GWS_RESPONSE_SCHEMA.get("properties", {})
    return all(
        field_name not in properties
        or _validate_property_value(field_value, properties[field_name])
        for field_name, field_value in response.items()
    )
