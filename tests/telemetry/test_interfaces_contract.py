import inspect
from pathlib import Path

import pytest

from openminion.modules.telemetry.interfaces import (
    TELEMETRY_INTERFACE_VERSION,
    ensure_telemetry_interface_compatibility,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.telemetry.storage.base import TelemetryStore
from openminion.modules.telemetry.storage.store import SQLiteTelemetryStore


class TestTelemetryInterfaceContract:
    def test_telemetry_interface_version_constant(self):
        assert TELEMETRY_INTERFACE_VERSION == "v1"
        assert isinstance(TELEMETRY_INTERFACE_VERSION, str)

    def test_telemetry_version_compatibility_check_positive(self):
        result = ensure_telemetry_interface_compatibility("v1")
        assert result is True

    def test_telemetry_version_compatibility_check_negative(self):
        with pytest.raises(ValueError) as exc_info:
            ensure_telemetry_interface_compatibility("v0")
        assert "Telemetry interface version mismatch" in str(exc_info.value)
        assert "v1" in str(exc_info.value)
        assert "v0" in str(exc_info.value)

    def test_telemetry_runtime_implements_contract(self, tmp_path: Path):
        service = TelemetryService(db_path=tmp_path / ".openminion" / "telemetry.db")
        assert hasattr(service, "contract_version")
        assert service.contract_version == "v1"
        assert hasattr(service, "get_module_summary")
        assert hasattr(service, "get_event_high_water")
        assert hasattr(service, "get_event_page")
        assert hasattr(service, "find_turn_invocation_ids")

        ctl = TelemetryCtl(service)
        assert hasattr(ctl, "contract_version")
        assert ctl.contract_version == "v1"
        assert hasattr(ctl, "emit_module_stats")
        assert hasattr(ctl, "emit_module_operation")
        assert hasattr(ctl, "emit_module_counter")
        assert hasattr(ctl, "emit_tool_exec_operation")
        assert hasattr(ctl, "emit_canonical_event_sync")

    def test_storage_contract_signatures_match_implementation(self):
        for method_name in (
            "insert_event",
            "insert_event_if_absent",
            "fetch_session_events",
            "fetch_invocation_events",
            "fetch_execution_events",
            "event_high_water",
            "fetch_event_page",
            "find_turn_invocation_ids",
        ):
            assert inspect.signature(getattr(TelemetryStore, method_name)) == (
                inspect.signature(getattr(SQLiteTelemetryStore, method_name))
            )

    def test_telemetry_compatibility_with_current_implementation(self, tmp_path: Path):
        service = TelemetryService(db_path=tmp_path / ".openminion" / "telemetry.db")
        result = ensure_telemetry_interface_compatibility(service.contract_version)
        assert result is True

        ctl = TelemetryCtl(service)
        result_ctl = ensure_telemetry_interface_compatibility(ctl.contract_version)
        assert result_ctl is True


class TestTelemetryInterfaceContractNegative:
    def test_telemetry_contract_violation_detection(self):
        fake_version = "v0"
        with pytest.raises(ValueError) as exc_info:
            ensure_telemetry_interface_compatibility(fake_version)

        error_msg = str(exc_info.value)
        assert "Telemetry interface version mismatch" in error_msg
        assert "v1" in error_msg
        assert "v0" in error_msg
