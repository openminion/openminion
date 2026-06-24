import pytest
from openminion.modules.brain.runtime.reasoning import (
    THINKING_INTERFACE_VERSION,
    ThinkingCtl,
    ThinkingRequest,
    ThinkingResolutionInput,
    ensure_thinking_compatibility,
)


class TestThinkingServiceContractVersion:
    def test_thinking_service_contract_version_declared(self):
        thinking_ctl = ThinkingCtl()
        assert hasattr(thinking_ctl, "contract_version")
        assert thinking_ctl.contract_version == THINKING_INTERFACE_VERSION


class TestThinkingServiceCompatibilityValidator:
    def test_thinking_service_valid_implementation_passes(self):
        thinking_ctl = ThinkingCtl()
        success, errors = ensure_thinking_compatibility(thinking_ctl, strict=False)
        assert success is True
        assert len(errors) == 0

    def test_thinking_service_exposes_new_resolution_contract(self):
        ctl = ThinkingCtl()

        resolved = ctl.resolve(
            request=ThinkingRequest(
                purpose="unit_test",
                requested_profile="detailed",
                provider="openai",
                model="MiniMax-M2.5",
            ),
            layers=ThinkingResolutionInput(code_default_profile="minimal"),
        )

        metadata = ctl.build_provider_metadata(resolved=resolved)
        hints = ctl.build_context_hints(resolved=resolved)

        assert resolved.reasoning_profile == "detailed"
        assert metadata["thinking_reasoning_profile"] == "detailed"
        assert hints["thinking_effective_profile"] == "detailed"

    def test_thinking_service_missing_method_fails(self):

        class BrokenCtl:
            contract_version = THINKING_INTERFACE_VERSION
            # Missing required methods like is_enabled, get_version, resolve

        ctl = BrokenCtl()
        success, errors = ensure_thinking_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert any("Missing required method" in error for error in errors)

    def test_thinking_service_version_mismatch_fails(self):

        class WrongVersionCtl:
            contract_version = "v99"  # Wrong version

        ctl = WrongVersionCtl()
        success, errors = ensure_thinking_compatibility(ctl, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_thinking_service_strict_mode_raises_error(self):

        class BadCtl:
            contract_version = "v99"  # Wrong version

        ctl = BadCtl()
        with pytest.raises(Exception):  # ThinkingError will be raised
            ensure_thinking_compatibility(ctl, strict=True)
