import pytest
from openminion.tools.file.interfaces import (
    CONTRACT_VERSION,
    FileRequestEnvelope,
    FileResultEnvelope,
    FileErrorEnvelope,
    FileOperationSchema,
    validate_contract_version,
    is_compatible,
)
from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolRequestEnvelope,
    ToolResultEnvelope,
    ToolErrorEnvelope,
)


def test_file_plugin_interface_baseline():
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert CONTRACT_VERSION == "v1"


def test_file_plugin_inherits_base_validators():
    assert validate_contract_version("v1") is True
    assert validate_contract_version("v1.0") is True
    assert validate_contract_version("v2") is True
    assert validate_contract_version("invalid") is False

    assert is_compatible("v1", CONTRACT_VERSION) is True
    assert ContractValidator.is_compatible("v1.1", CONTRACT_VERSION) is True


def test_file_request_envelope_inheritance():
    req = FileRequestEnvelope(
        method="file.read", args={"path": "/test.txt"}, contract_version="v1"
    )

    assert req.method == "file.read"
    assert req.args == {"path": "/test.txt"}
    assert req.contract_version == "v1"

    base_req = ToolRequestEnvelope(
        method="file.read", args={"path": "/test.txt"}, contract_version="v1"
    )

    assert req.method == base_req.method
    assert req.args == base_req.args


def test_file_result_envelope_inheritance():
    result = FileResultEnvelope(
        status="ok",
        data={"content": "test content"},
        artifacts={"file1": "ref1"},
        contract_version="v1.0",
    )

    assert result.status == "ok"
    assert result.data == {"content": "test content"}
    assert result.artifacts == {"file1": "ref1"}
    assert result.contract_version == "v1.0"

    base_result = ToolResultEnvelope(
        status="ok",
        data={"content": "test content"},
        artifacts={"file1": "ref1"},
        contract_version="v1.0",
    )

    assert result.status == base_result.status
    assert result.data == base_result.data
    assert result.artifacts == base_result.artifacts


def test_file_error_envelope_inheritance():
    error = FileErrorEnvelope(
        error_code="FILE_NOT_FOUND",
        error_message="File does not exist",
        details={"path": "/missing.txt"},
        contract_version="v1.2",
    )

    assert error.error_code == "FILE_NOT_FOUND"
    assert error.error_message == "File does not exist"
    assert error.details == {"path": "/missing.txt"}
    assert error.contract_version == "v1.2"

    base_error = ToolErrorEnvelope(
        error_code="FILE_NOT_FOUND",
        error_message="File does not exist",
        details={"path": "/missing.txt"},
        contract_version="v1.2",
    )

    assert error.error_code == base_error.error_code
    assert error.error_message == base_error.error_message


def test_normalized_output_with_alias_compatibility():
    file_operation = FileOperationSchema(
        operation="read",
        parameters={"path": "/example.txt"},
        contract_version=CONTRACT_VERSION,
    )

    result = FileResultEnvelope(
        status="ok",
        data={"content": "file contents", "path": "/example.txt"},
        artifacts={"content_ref": "path:/example.txt"},
        contract_version=CONTRACT_VERSION,
    )

    assert "status" in result.model_dump()
    assert "data" in result.model_dump()
    assert "artifacts" in result.model_dump()
    assert "contract_version" in result.model_dump()

    assert ContractValidator.is_compatible(
        file_operation.contract_version, PLUGIN_CONTRACT_VERSION
    )


def test_positive_and_negative_contract_tests():
    valid_file_req = FileRequestEnvelope(
        method="file.list_dir", args={"path": "."}, contract_version=CONTRACT_VERSION
    )
    valid_result = FileResultEnvelope(
        status="ok", data={"entries": []}, contract_version=CONTRACT_VERSION
    )

    assert ContractValidator.validate_contract_version(valid_file_req.contract_version)
    assert ContractValidator.validate_contract_version(valid_result.contract_version)

    is_compat = ContractValidator.is_compatible(
        valid_file_req.contract_version, valid_result.contract_version
    )
    assert is_compat is True

    with pytest.raises(ValueError):
        FileRequestEnvelope(
            method="file.read",
            args={"path": "test.txt"},
            contract_version="invalid-version",
        )

    with pytest.raises(ValueError):
        FileResultEnvelope(
            status="error",
            data={},
            contract_version="bad-format",
        )


def test_mismatch_path_fails_deterministically():
    FileResultEnvelope(
        status="ok",
        data={"test": "value"},
        contract_version="v1",
    )

    with pytest.raises(ValueError):
        FileResultEnvelope(
            status="ok",
            data={"test": "value"},
            contract_version="not-a-valid-format",
        )
