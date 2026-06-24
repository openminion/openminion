import pytest
from unittest.mock import Mock
from openminion.modules.brain.loop.recursive.contracts import (
    RLM_INTERFACE_VERSION,
    ensure_rlm_compatibility,
)
from openminion.modules.brain.loop.recursive.service import RLMService


class TestRlmServiceContractVersion:
    def test_rlm_service_contract_version_declared(self):
        # Create mock clients for RLMService parameters
        mock_sessctl = Mock()
        mock_contextctl = Mock()
        mock_llmctl = Mock()

        service = RLMService(
            sessctl=mock_sessctl, contextctl=mock_contextctl, llmctl=mock_llmctl
        )
        assert hasattr(service, "contract_version")
        assert service.contract_version == RLM_INTERFACE_VERSION


class TestRlmServiceCompatibilityValidator:
    def test_rlm_service_valid_implementation_passes(self):
        # Create mock clients for RLMService parameters
        mock_sessctl = Mock()
        mock_contextctl = Mock()
        mock_llmctl = Mock()

        service = RLMService(
            sessctl=mock_sessctl, contextctl=mock_contextctl, llmctl=mock_llmctl
        )
        success, errors = ensure_rlm_compatibility(service, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_rlm_service_missing_method_fails(self):

        class BrokenService:
            contract_version = RLM_INTERFACE_VERSION
            # Missing required methods like generate, retrieve, etc.

        service = BrokenService()
        success, errors = ensure_rlm_compatibility(service, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_rlm_service_version_mismatch_fails(self):

        class WrongVersionService:
            contract_version = "v99"  # Wrong version

        service = WrongVersionService()
        success, errors = ensure_rlm_compatibility(service, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_rlm_service_strict_mode_raises_error(self):

        class BadService:
            contract_version = "v99"  # Wrong version

        service = BadService()
        with pytest.raises(Exception):  # RlmError will be raised
            ensure_rlm_compatibility(service, strict=True)
