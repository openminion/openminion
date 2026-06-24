from openminion.tools.browser.providers.pinchtab.schemas import InstanceStartArgs


def test_instance_start_args_valid_modes():
    # Test valid modes
    args_headed = InstanceStartArgs(mode="headed")
    assert args_headed.mode == "headed"

    args_headless = InstanceStartArgs(mode="headless")
    assert args_headless.mode == "headless"

    # Test with profile_id as well
    args_both = InstanceStartArgs(profile_id="test-profile", mode="headed")
    assert args_both.profile_id == "test-profile"
    assert args_both.mode == "headed"


def test_instance_start_args_optional_mode():
    # Test that mode is optional (should be None by default)
    args_default = InstanceStartArgs()
    assert args_default.mode is None
    assert args_default.profile_id is None

    args_profile_only = InstanceStartArgs(profile_id="test-profile")
    assert args_profile_only.profile_id == "test-profile"
    assert args_profile_only.mode is None


def test_instance_start_args_validation_error_for_invalid_mode():
    # This should throw a pydantic ValidationError
    import pydantic

    try:
        args_invalid = InstanceStartArgs(mode="invalid_mode")
        print(f"ERROR: Expected validation to fail but got: {args_invalid}")
        assert False, "Expected validation error for invalid mode"
    except (pydantic.ValidationError, ValueError) as e:
        # Ensure it's a validation error and not a runtime crash
        assert "mode" in str(e) or "enum" in str(e).lower()
        print(f"Correctly caught validation error: {e}")
        pass  # This is expected


def test_instance_start_args_allows_valid_modes_only():
    import pydantic

    # Should work
    InstanceStartArgs(mode="headed")
    InstanceStartArgs(mode="headless")

    # Should not work
    try:
        InstanceStartArgs(mode="invalid")
        assert False, "Should have raised validation error"
    except (pydantic.ValidationError, ValueError):
        pass  # Expected
