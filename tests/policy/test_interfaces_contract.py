import pytest
from unittest.mock import Mock
from openminion.modules.policy.interfaces import (
    POLICY_INTERFACE_VERSION,
    ensure_policy_compatibility,
)
from openminion.modules.policy.runtime.service import PolicyCtl


class TestPolicyCtlContractVersion:
    def test_policy_ctl_contract_version_declared(self):
        # Create a mock store for PolicyCtl
        mock_store = Mock()
        mock_store.close = Mock()
        mock_store.get_setting = Mock(return_value=None)
        mock_store.set_setting = Mock()
        mock_store.create_grant = Mock()
        mock_store.revoke_grant = Mock()
        mock_store.list_grants = Mock()
        mock_store.cleanup_expired = Mock()
        mock_store.list_decisions = Mock()
        mock_store.consume_grant_use = Mock()
        mock_store.log_decision = Mock()

        ctl = PolicyCtl(store=mock_store)
        assert hasattr(ctl, "contract_version")
        assert ctl.contract_version == POLICY_INTERFACE_VERSION


class TestPolicyCtlCompatibilityValidator:
    def test_policy_ctl_valid_implementation_passes(self):
        # Create a mock store for PolicyCtl
        mock_store = Mock()
        mock_store.close = Mock()
        mock_store.get_setting = Mock(return_value=None)
        mock_store.set_setting = Mock()
        mock_store.create_grant = Mock(return_value="grant-id")
        mock_store.revoke_grant = Mock(return_value=True)
        mock_store.list_grants = Mock(return_value=[])
        mock_store.cleanup_expired = Mock()
        mock_store.list_decisions = Mock(return_value=[])
        mock_store.consume_grant_use = Mock()
        mock_store.log_decision = Mock()

        ctl = PolicyCtl(store=mock_store)
        success, errors = ensure_policy_compatibility(ctl, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_policy_ctl_missing_method_fails(self):

        class BrokenCtl:
            contract_version = POLICY_INTERFACE_VERSION
            # Missing required methods like check, create_grant, etc.

        ctl = BrokenCtl()
        success, errors = ensure_policy_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_policy_ctl_version_mismatch_fails(self):

        class WrongVersionCtl:
            contract_version = "v99"  # Wrong version

        ctl = WrongVersionCtl()
        success, errors = ensure_policy_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_policy_ctl_strict_mode_raises_error(self):

        class BadCtl:
            contract_version = "v99"  # Wrong version

        ctl = BadCtl()
        with pytest.raises(Exception):  # PolicyError will be raised
            ensure_policy_compatibility(ctl, strict=True)
