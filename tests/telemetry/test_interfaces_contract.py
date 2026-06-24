import os
import tempfile

import pytest

from openminion.modules.telemetry.interfaces import (
    TELEMETRY_INTERFACE_VERSION,
    ensure_telemetry_interface_compatibility,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


class TestTelemetryInterfaceContract:
    def test_telemetry_interface_version_constant(self):
        assert TELEMETRY_INTERFACE_VERSION == "v1"
        assert isinstance(TELEMETRY_INTERFACE_VERSION, str)

    def test_telemetry_version_compatibility_check_positive(self):
        # Should not raise an exception
        result = ensure_telemetry_interface_compatibility("v1")
        assert result is True

    def test_telemetry_version_compatibility_check_negative(self):
        with pytest.raises(ValueError) as exc_info:
            ensure_telemetry_interface_compatibility("v0")
        assert "Telemetry interface version mismatch" in str(exc_info.value)
        assert "v1" in str(exc_info.value)
        assert "v0" in str(exc_info.value)

    def test_telemetry_runtime_implements_contract(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
            try:
                # Create minimal service instance with temp db
                service = TelemetryService(db_path=tmp_file.name)

                # Verify it has the required contract attribute
                assert hasattr(service, "contract_version")
                assert service.contract_version == "v1"
                assert hasattr(service, "get_module_summary")

                # Test the adapter as well
                ctl = TelemetryCtl(service)
                assert hasattr(ctl, "contract_version")
                assert ctl.contract_version == "v1"
                assert hasattr(ctl, "emit_module_stats")
                assert hasattr(ctl, "emit_module_operation")
                assert hasattr(ctl, "emit_module_counter")
                assert hasattr(ctl, "emit_tool_exec_operation")
            finally:
                if os.path.exists(tmp_file.name):
                    os.remove(tmp_file.name)

    def test_telemetry_compatibility_with_current_implementation(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
            try:
                service = TelemetryService(db_path=tmp_file.name)

                # Verify the contract version is compatible
                result = ensure_telemetry_interface_compatibility(
                    service.contract_version
                )
                assert result is True

                # Test adapter as well
                ctl = TelemetryCtl(service)
                result_ctl = ensure_telemetry_interface_compatibility(
                    ctl.contract_version
                )
                assert result_ctl is True
            finally:
                if os.path.exists(tmp_file.name):
                    os.remove(tmp_file.name)


class TestTelemetryInterfaceContractNegative:
    def test_telemetry_contract_violation_detection(self):
        fake_version = "v0"  # Different from expected "v1"
        with pytest.raises(ValueError) as exc_info:
            ensure_telemetry_interface_compatibility(fake_version)

        error_msg = str(exc_info.value)
        assert "Telemetry interface version mismatch" in error_msg
        assert "v1" in error_msg  # Expected version
        assert "v0" in error_msg  # Actual version
