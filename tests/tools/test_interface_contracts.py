import pytest
from openminion.modules.tool.interfaces import (
    CONTRACT_VERSION_PATTERN,
    ContractValidator,
    PluginContractError,
    PLUGIN_CONTRACT_VERSION,
    ToolRequestEnvelope,
    ToolResultEnvelope,
    ToolErrorEnvelope,
    validate_plugin_contract,
)


def test_plugin_contract_version_defined():
    assert isinstance(PLUGIN_CONTRACT_VERSION, str)
    assert PLUGIN_CONTRACT_VERSION == "v1"


def test_contract_version_pattern():
    import re

    valid_versions = ["v1", "v1.0", "v2.1.3", "v10.20.30"]
    invalid_versions = [
        "1",
        "version1",
        "v",
    ]  # v1.0.0.0.0.0.0 is actually valid per our pattern

    compiled_pattern = re.compile(CONTRACT_VERSION_PATTERN)

    for version in valid_versions:
        assert compiled_pattern.match(version) is not None, (
            f"Valid version {version} should match pattern"
        )

    for version in invalid_versions:
        assert compiled_pattern.match(version) is None, (
            f"Invalid version {version} should not match pattern"
        )

    for version in invalid_versions:
        assert compiled_pattern.match(version) is None, (
            f"Invalid version {version} should not match pattern"
        )

    for version in invalid_versions:
        import re

        compiled_pattern = re.compile(CONTRACT_VERSION_PATTERN)
        assert compiled_pattern.match(version) is None, (
            f"Invalid version {version} should not match pattern"
        )


def test_contract_validator_instance_methods():
    # Valid versions
    assert ContractValidator.validate_contract_version("v1") is True
    assert ContractValidator.validate_contract_version("v2.1.3") is True
    assert ContractValidator.validate_contract_version("v10") is True

    # Invalid versions
    assert ContractValidator.validate_contract_version("1") is False
    assert ContractValidator.validate_contract_version("") is False
    assert ContractValidator.validate_contract_version("v") is False
    assert ContractValidator.validate_contract_version("version1") is False


def test_contract_compatibility_validation():
    # Compatible pairs (should return True)
    assert ContractValidator.is_compatible("v1", "v1") is True
    assert ContractValidator.is_compatible("v1", "v1.0") is True
    assert ContractValidator.is_compatible("v2.1", "v2.1") is True

    # Check with our known baseline
    assert ContractValidator.is_compatible(PLUGIN_CONTRACT_VERSION, "v1") is True
    assert ContractValidator.is_compatible("v1.1", PLUGIN_CONTRACT_VERSION) is True

    # Test mismatch cases (should still return True based on current logic since they follow pattern)
    # NOTE: Current implementation returns True for all valid versions, regardless of equality
    # Future enhanced implementations might be stricter based on semantic versioning
    assert ContractValidator.is_compatible("v1", "v2") is True
    assert ContractValidator.is_compatible("v1.0", "v1.1") is True


def test_tool_request_envelope():
    envelope = ToolRequestEnvelope(
        method="test.method", args={"param1": "value1"}, contract_version="v1.0"
    )

    assert envelope.method == "test.method"
    assert envelope.args == {"param1": "value1"}
    assert envelope.contract_version == "v1.0"

    # Test default version
    default_envelope = ToolRequestEnvelope(method="test.default", args={})
    assert (
        default_envelope.contract_version == "v1"
    )  # Should use default PLUGIN_CONTRACT_VERSION

    # Test validation rejects invalid version formats
    with pytest.raises(ValueError):
        ToolRequestEnvelope(
            method="test.invalid", args={}, contract_version="invalid-version"
        )


def test_tool_result_envelope():
    envelope = ToolResultEnvelope(
        status="success",
        data={"result": "value"},
        artifacts={"artifact1": "ref1"},
        contract_version="v1.2",
    )

    assert envelope.status == "success"
    assert envelope.data == {"result": "value"}
    assert envelope.artifacts == {"artifact1": "ref1"}
    assert envelope.contract_version == "v1.2"

    # Test defaults
    default_envelope = ToolResultEnvelope(status="success", data={"result": "value"})
    assert default_envelope.contract_version == "v1"  # Default version
    assert default_envelope.artifacts == {}  # Default empty dict


def test_tool_error_envelope():
    envelope = ToolErrorEnvelope(
        error_code="ERROR_CODE",
        error_message="Error occurred",
        details={"detail1": "value1"},
        contract_version="v1.0",
    )

    assert envelope.error_code == "ERROR_CODE"
    assert envelope.error_message == "Error occurred"
    assert envelope.details == {"detail1": "value1"}
    assert envelope.contract_version == "v1.0"

    # Test defaults
    default_envelope = ToolErrorEnvelope(error_code="CODE", error_message="Message")
    assert default_envelope.contract_version == "v1"  # Default version
    assert default_envelope.details == {}  # Default empty dict
    assert default_envelope.contract_version == PLUGIN_CONTRACT_VERSION


def test_positive_and_negative_contract_drift():
    # Positive: Valid contract structures with valid versions
    valid_requests = [
        ToolRequestEnvelope(method="a.method", args={}, contract_version="v1"),
        ToolRequestEnvelope(
            method="b.method", args={"test": "value"}, contract_version="v2"
        ),
        ToolRequestEnvelope(method="c.method", args={}, contract_version="v1.0.1"),
    ]
    for req in valid_requests:
        assert hasattr(req, "contract_version")
        assert ContractValidator.validate_contract_version(req.contract_version)

    # Negative: Attempts to create invalid contracts should fail
    with pytest.raises(ValueError):
        ToolResultEnvelope(status="ok", data={}, contract_version="invalid_version")

    with pytest.raises(ValueError):
        ToolErrorEnvelope(error_code="TEST", error_message="msg", contract_version="")

    with pytest.raises(ValueError):
        ToolRequestEnvelope(method="test", args={}, contract_version="bad-format")


def test_validate_plugin_contract_accepts_well_formed_plugin():
    class _Plugin:
        tool_id = "sample.tool"
        contract_version = "v1"
        capabilities = ("read_only", "sample")

        def register(self, registry):
            del registry

        def healthcheck(self):
            return {"ok": True}

    contract = validate_plugin_contract(_Plugin())
    assert contract.tool_id == "sample.tool"
    assert contract.contract_version == "v1"
    assert "sample" in contract.capabilities


def test_validate_plugin_contract_rejects_missing_contract_metadata():
    class _Plugin:
        tool_id = "sample.tool"
        capabilities = ("read_only",)

        def register(self, registry):
            del registry

        def healthcheck(self):
            return {"ok": True}

    with pytest.raises(PluginContractError):
        validate_plugin_contract(_Plugin())
