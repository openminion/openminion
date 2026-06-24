import pytest
from unittest.mock import Mock
from openminion.modules.identity.interfaces import (
    IDENTITY_INTERFACE_VERSION,
    IDENTITY_REPOSITORY_INTERFACE_VERSION,
    ensure_identity_repository_compatibility,
    ensure_identity_compatibility,
)
from openminion.modules.identity.runtime.service import IdentityCtl


class TestIdentityCtlContractVersion:
    def test_identity_ctl_contract_version_declared(self):
        # Create a minimal mock store for IdentityCtl
        mock_store = Mock()
        mock_store.close = Mock()
        mock_store.list_profiles = Mock(return_value=[])
        mock_store.upsert_profile = Mock()
        mock_store.delete_profile = Mock()
        mock_store.get_profile = Mock()
        mock_store.get_cached_snippet = Mock()
        mock_store.upsert_cached_snippet = Mock()
        mock_store.clear_cache = Mock()
        mock_store.update_profile_version = Mock()

        ctl = IdentityCtl(store=mock_store)
        assert hasattr(ctl, "contract_version")
        assert ctl.contract_version == IDENTITY_INTERFACE_VERSION


class TestIdentityCtlCompatibilityValidator:
    def test_identity_ctl_valid_implementation_passes(self):
        # Create a minimal mock store for IdentityCtl
        mock_store = Mock()
        mock_store.close = Mock()
        mock_store.list_profiles = Mock(return_value=[])
        mock_store.upsert_profile = Mock()
        mock_store.delete_profile = Mock()
        mock_store.get_profile = Mock()
        mock_store.get_cached_snippet = Mock()
        mock_store.upsert_cached_snippet = Mock()
        mock_store.clear_cache = Mock()
        mock_store.update_profile_version = Mock()

        ctl = IdentityCtl(store=mock_store)
        success, errors = ensure_identity_compatibility(ctl, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_identity_ctl_missing_method_fails(self):

        class BrokenCtl:
            contract_version = IDENTITY_INTERFACE_VERSION
            # Missing required methods like get_profile, list_profiles, etc.

        ctl = BrokenCtl()
        success, errors = ensure_identity_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_identity_ctl_version_mismatch_fails(self):

        class WrongVersionCtl:
            contract_version = "v99"  # Wrong version

        ctl = WrongVersionCtl()
        success, errors = ensure_identity_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_identity_ctl_strict_mode_raises_error(self):

        class BadCtl:
            contract_version = "v99"  # Wrong version

        ctl = BadCtl()
        with pytest.raises(Exception):  # IdentityError will be raised
            ensure_identity_compatibility(ctl, strict=True)


class TestIdentityRepositoryCompatibilityValidator:
    def test_identity_repository_valid_implementation_passes(self):
        class _Repo:
            repository_contract_version = IDENTITY_REPOSITORY_INTERFACE_VERSION

            def get_profile(self, agent_id: str):
                return {"agent_id": agent_id}

            def upsert_profile(self, profile, actor=None, reason=None):
                return "v1"

        success, errors = ensure_identity_repository_compatibility(
            _Repo(), strict=False
        )
        assert success is True
        assert errors == []

    def test_identity_repository_missing_method_fails(self):
        class _BrokenRepo:
            repository_contract_version = IDENTITY_REPOSITORY_INTERFACE_VERSION

            def get_profile(self, agent_id: str):
                return {"agent_id": agent_id}

        success, errors = ensure_identity_repository_compatibility(
            _BrokenRepo(), strict=False
        )
        assert success is False
        assert any(
            "Missing required method: upsert_profile" in error for error in errors
        )
