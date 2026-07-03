import pytest
from pydantic import ValidationError

from openminion.tools.browser.providers.pinchtab.schemas import InstanceStartArgs


def test_instance_start_args_valid_modes():
    args_headed = InstanceStartArgs(mode="headed")
    assert args_headed.mode == "headed"

    args_headless = InstanceStartArgs(mode="headless")
    assert args_headless.mode == "headless"

    args_both = InstanceStartArgs(profile_id="test-profile", mode="headed")
    assert args_both.profile_id == "test-profile"
    assert args_both.mode == "headed"


def test_instance_start_args_optional_mode():
    args_default = InstanceStartArgs()
    assert args_default.mode is None
    assert args_default.profile_id is None

    args_profile_only = InstanceStartArgs(profile_id="test-profile")
    assert args_profile_only.profile_id == "test-profile"
    assert args_profile_only.mode is None


def test_instance_start_args_validation_error_for_invalid_mode():
    with pytest.raises((ValidationError, ValueError), match="mode|enum"):
        InstanceStartArgs(mode="invalid_mode")


def test_instance_start_args_allows_valid_modes_only():
    InstanceStartArgs(mode="headed")
    InstanceStartArgs(mode="headless")

    with pytest.raises((ValidationError, ValueError)):
        InstanceStartArgs(mode="invalid")
