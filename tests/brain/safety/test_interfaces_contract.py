import pytest
from openminion.modules.brain.runtime.safety import (
    SAFETY_INTERFACE_VERSION,
    SafetyAction,
    SafetyEvent,
    SafetyService,
    SafetyState,
    ensure_safety_interface_compatibility,
)


class TestSafetyInterfaceContract:
    def test_safety_interface_version_constant(self):
        assert SAFETY_INTERFACE_VERSION == "v1"
        assert isinstance(SAFETY_INTERFACE_VERSION, str)

    def test_safety_version_compatibility_check_positive(self):
        # Should not raise an exception
        result = ensure_safety_interface_compatibility("v1")
        assert result is True

    def test_safety_version_compatibility_check_negative(self):
        with pytest.raises(ValueError) as exc_info:
            ensure_safety_interface_compatibility("v0")
        assert "Safety interface version mismatch" in str(exc_info.value)
        assert "v1" in str(exc_info.value)
        assert "v0" in str(exc_info.value)

    def test_safety_runtime_implements_contract(self):
        service = SafetyService()
        # Verify it has the required contract attribute
        assert hasattr(service, "contract_version")
        assert service.contract_version == "v1"

    def test_safety_event_structure(self):
        event = SafetyEvent(
            action=SafetyAction.STOP,
            state_before=SafetyState.NORMAL,
            state_after=SafetyState.STOPPING,
            reason="test reason",
        )

        # Check that all required fields are present
        assert hasattr(event, "action")
        assert hasattr(event, "state_before")
        assert hasattr(event, "state_after")
        assert hasattr(event, "reason")
        assert hasattr(event, "session_id")
        assert hasattr(event, "metadata")

        # Check that these field types are correct
        assert isinstance(event.action, SafetyAction)
        assert isinstance(event.state_before, SafetyState)
        assert isinstance(event.state_after, SafetyState)

    def test_safety_service_basic_functionality(self):
        service = SafetyService()

        # Initial state should be normal
        assert service.state == SafetyState.NORMAL

        # Stop operation should work when in normal state
        result = service.stop(reason="test stop")
        assert result is True
        assert service.state == SafetyState.STOPPED

        # After stop, should not be normal
        assert not service.is_normal()

    def test_safety_compatibility_with_current_implementation(self):
        service = SafetyService()
        # Verify the contract version is compatible
        result = ensure_safety_interface_compatibility(service.contract_version)
        assert result is True


class TestSafetyInterfaceContractNegative:
    def test_safety_contract_violation_detection(self):
        fake_version = "v0"  # Different from expected "v1"
        with pytest.raises(ValueError) as exc_info:
            ensure_safety_interface_compatibility(fake_version)

        error_msg = str(exc_info.value)
        assert "Safety interface version mismatch" in error_msg
        assert "v1" in error_msg  # Expected version
        assert "v0" in error_msg  # Actual version
