from __future__ import annotations


from openminion.tools.gws.interfaces import (
    validate_request_schema,
    validate_response_schema,
)


def test_request_schema_valid_calls() -> None:
    # Test GWS call schema validation
    valid_call_request = {
        "service": "drive",
        "resource_path": ["files"],
        "method": "list",
        "params": {"pageSize": 10},
        "dry_run": False,
        "timeout_seconds": 30.0,
    }
    assert validate_request_schema("gws.call", valid_call_request) is True

    # Test GWS schema validation
    valid_schema_request = {"method_full": "drive.files.list", "timeout_seconds": 30.0}
    assert validate_request_schema("gws.schema", valid_schema_request) is True

    # Test GWS auth request validation
    valid_auth_request = {"timeout_seconds": 30.0}
    assert validate_request_schema("gws.auth.setup", valid_auth_request) is True
    assert validate_request_schema("gws.auth.login", valid_auth_request) is True

    # Test GWS auth export request validation
    valid_auth_export_request = {
        "output_path": "credentials.json",
        "overwrite": True,
        "timeout_seconds": 30.0,
    }
    assert validate_request_schema("gws.auth.export", valid_auth_export_request) is True


def test_request_schema_invalid_call_missing_required_field() -> None:
    invalid_call_request = {
        # Missing required service field
        "resource_path": ["files"],
        "method": "list",
    }
    # Should fail validation due to missing required field
    assert validate_request_schema("gws.call", invalid_call_request) is False


def test_request_schema_invalid_operation_name() -> None:
    # Test with an unknown operation that should fail
    valid_request = {
        "service": "drive",
        "resource_path": ["files"],
        "method": "list",
    }
    assert validate_request_schema("gws_unknown_operation", valid_request) is False


def test_response_schema_with_complete_payload() -> None:
    valid_response = {
        "ok": True,
        "source": "gws",
        "content": "API call succeeded",
        "data": {"files": [{"id": "file123", "name": "Document"}]},
        "raw_stdout": "some output",
        "raw_stderr": "",
        "metrics": {"duration_ms": 123, "exit_code": 0, "timed_out": False},
        "risk": "read",
    }
    assert validate_response_schema(valid_response) is True


def test_response_schema_with_minimal_payload() -> None:
    minimal_response = {"ok": False, "source": "gws", "content": "operation failed"}
    assert validate_response_schema(minimal_response) is True


def test_response_schema_negative_invalid_missing_fields() -> None:
    invalid_response = {
        # Missing 'ok', 'source', 'content' required fields
        "data": {"files": []}
    }
    assert validate_response_schema(invalid_response) is False


def test_response_schema_negative_invalid_types() -> None:
    response_with_invalid_types = {
        "ok": "not a boolean",  # Should be boolean
        "source": "gws",
        "content": "API call succeeded",
    }
    assert validate_response_schema(response_with_invalid_types) is False


def test_request_schema_enforces_field_types() -> None:
    invalid_call_request = {
        "service": 123,  # Should be string, not number
        "resource_path": ["files"],
        "method": "list",
    }
    assert validate_request_schema("gws.call", invalid_call_request) is False


def test_request_schema_enforces_numeric_constraints() -> None:
    invalid_call_request = {
        "service": "drive",
        "resource_path": ["files"],
        "method": "list",
        "timeout_seconds": -10,  # Should be >= 0
    }
    assert validate_request_schema("gws.call", invalid_call_request) is False


def test_request_schema_enforces_array_items_types() -> None:
    invalid_call_request = {
        "service": "drive",
        "resource_path": ["files", 123],  # Array elements should be strings, not mixed
        "method": "list",
    }
    assert validate_request_schema("gws.call", invalid_call_request) is False
