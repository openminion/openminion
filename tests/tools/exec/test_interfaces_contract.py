import pytest
from openminion.tools.exec.interfaces import (
    CONTRACT_VERSION,
    ExecRequestEnvelope,
    ExecResultEnvelope,
    ExecErrorEnvelope,
    ExecOperationSchema,
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


def test_exec_plugin_interface_baseline():
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert CONTRACT_VERSION == "v1"


def test_exec_plugin_inherits_base_validators():
    assert validate_contract_version("v1") is True
    assert validate_contract_version("v1.0") is True
    assert validate_contract_version("v2") is True
    assert validate_contract_version("invalid") is False

    assert is_compatible("v1", CONTRACT_VERSION) is True
    assert ContractValidator.is_compatible("v1.1", CONTRACT_VERSION) is True


def test_exec_request_envelope_inheritance():
    req = ExecRequestEnvelope(
        method="exec.run", args={"command": "ls -la"}, contract_version="v1"
    )

    assert req.method == "exec.run"
    assert req.args == {"command": "ls -la"}
    assert req.contract_version == "v1"

    base_req = ToolRequestEnvelope(
        method="exec.run", args={"command": "ls -la"}, contract_version="v1"
    )

    assert req.method == base_req.method
    assert req.args == base_req.args


def test_exec_result_envelope_inheritance():
    result = ExecResultEnvelope(
        status="ok",
        data={"output": "listing", "exit_code": 0},
        artifacts={"output1": "ref1"},
        contract_version="v1.0",
    )

    assert result.status == "ok"
    assert result.data == {"output": "listing", "exit_code": 0}
    assert result.artifacts == {"output1": "ref1"}
    assert result.contract_version == "v1.0"

    base_result = ToolResultEnvelope(
        status="ok",
        data={"output": "listing", "exit_code": 0},
        artifacts={"output1": "ref1"},
        contract_version="v1.0",
    )

    assert result.status == base_result.status
    assert result.data == base_result.data
    assert result.artifacts == base_result.artifacts


def test_exec_error_envelope_inheritance():
    error = ExecErrorEnvelope(
        error_code="COMMAND_FAILED",
        error_message="Command terminated with exit code 1",
        details={"exit_code": 1, "command": "failed-command"},
        contract_version="v1.2",
    )

    assert error.error_code == "COMMAND_FAILED"
    assert error.error_message == "Command terminated with exit code 1"
    assert error.details == {"exit_code": 1, "command": "failed-command"}
    assert error.contract_version == "v1.2"

    base_error = ToolErrorEnvelope(
        error_code="COMMAND_FAILED",
        error_message="Command terminated with exit code 1",
        details={"exit_code": 1, "command": "failed-command"},
        contract_version="v1.2",
    )

    assert error.error_code == base_error.error_code
    assert error.error_message == base_error.error_message


def test_normalized_output_with_alias_compatibility():
    exec_operation = ExecOperationSchema(
        operation="run",
        parameters={"command": "echo hello"},
        contract_version=CONTRACT_VERSION,
    )

    result = ExecResultEnvelope(
        status="ok",
        data={"output": "hello", "command": "echo hello"},
        artifacts={"cmd_output": "path:output.txt"},
        contract_version=CONTRACT_VERSION,
    )

    assert "status" in result.model_dump()
    assert "data" in result.model_dump()
    assert "artifacts" in result.model_dump()
    assert "contract_version" in result.model_dump()

    assert ContractValidator.is_compatible(
        exec_operation.contract_version, PLUGIN_CONTRACT_VERSION
    )


def test_positive_and_negative_contract_tests():
    valid_exec_req = ExecRequestEnvelope(
        method="exec.run", args={"command": "date"}, contract_version=CONTRACT_VERSION
    )
    valid_result = ExecResultEnvelope(
        status="ok",
        data={"output": "Sun Jan 1 12:00:00 UTC 2023"},
        contract_version=CONTRACT_VERSION,
    )

    assert ContractValidator.validate_contract_version(valid_exec_req.contract_version)
    assert ContractValidator.validate_contract_version(valid_result.contract_version)

    is_compat = ContractValidator.is_compatible(
        valid_exec_req.contract_version, valid_result.contract_version
    )
    assert is_compat is True

    with pytest.raises(ValueError):
        ExecRequestEnvelope(
            method="exec.run",
            args={"command": "ls"},
            contract_version="invalid-version",
        )

    with pytest.raises(ValueError):
        ExecResultEnvelope(
            status="error",
            data={},
            contract_version="bad-format",
        )


def test_mismatch_path_fails_deterministically():
    with pytest.raises(ValueError):
        ExecResultEnvelope(
            status="ok",
            data={"test": "value"},
            contract_version="not-a-valid-format",
        )
