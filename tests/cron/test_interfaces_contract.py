import pytest
from unittest.mock import Mock
from openminion.modules.task.scheduling.interfaces import (
    CRON_INTERFACE_VERSION,
    CronError,
    ensure_cron_store_compatibility,
)
from openminion.services.cron.scheduler import CronScheduler


class TestCronSchedulerContractVersion:
    def test_cron_scheduler_contract_version_declared(self):
        # Create mock store that meets the protocol requirements
        mock_store = Mock()
        mock_store.enqueue_due_cron_runs = Mock(return_value=[])
        mock_store.acquire_cron_runs = Mock(return_value=[])
        mock_store.renew_cron_run_lease = Mock(return_value=True)
        mock_store.get_cron_job = Mock(return_value=None)
        mock_store.finish_cron_run = Mock(return_value=None)
        mock_store.contract_version = (
            CRON_INTERFACE_VERSION  # Add contract version for store
        )

        scheduler = CronScheduler(store=mock_store)
        assert hasattr(scheduler, "contract_version")
        assert scheduler.contract_version == CRON_INTERFACE_VERSION


class TestCronStoreCompatibilityValidator:
    def test_cron_store_valid_implementation_passes(self):
        # Create mock store that meets the protocol requirements
        mock_store = Mock()
        mock_store.add_cron_job = Mock(return_value="job-1")
        mock_store.enqueue_due_cron_runs = Mock(return_value=[])
        mock_store.acquire_cron_runs = Mock(return_value=[])
        mock_store.renew_cron_run_lease = Mock(return_value=True)
        mock_store.get_cron_job = Mock(return_value=None)
        mock_store.list_cron_jobs = Mock(return_value=[])
        mock_store.delete_cron_job = Mock(return_value=None)
        mock_store.list_cron_runs = Mock(return_value=[])
        mock_store.finish_cron_run = Mock(return_value=None)
        mock_store.contract_version = (
            CRON_INTERFACE_VERSION  # Add contract version for store
        )

        success, errors = ensure_cron_store_compatibility(mock_store, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_cron_store_missing_method_fails(self):

        class BrokenStore:
            contract_version = CRON_INTERFACE_VERSION
            # Missing required methods like enqueue_due_cron_runs, acquire_cron_runs, etc.

        store = BrokenStore()
        success, errors = ensure_cron_store_compatibility(store, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required store method" in error for error in errors)

    def test_cron_store_version_mismatch_fails(self):

        class WrongVersionStore:
            contract_version = "v99"  # Wrong version

        store = WrongVersionStore()
        success, errors = ensure_cron_store_compatibility(store, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_cron_store_strict_mode_raises_error(self):

        class BadStore:
            contract_version = "v99"  # Wrong version

        store = BadStore()
        with pytest.raises(CronError) as exc_info:
            ensure_cron_store_compatibility(store, strict=True)
        assert exc_info.value.code == "CRON_STORE_INTERFACE_VIOLATION"
