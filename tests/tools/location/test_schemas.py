from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.tools.location.schemas import (
    LocationGetArgs,
    LocationGetIPArgs,
    LocationSetDefaultArgs,
)


def test_location_get_defaults() -> None:
    args = LocationGetArgs()
    assert args.prefer == "auto"
    assert args.max_privacy == "city"


def test_location_get_schema_describes_zero_arg_current_location() -> None:
    schema = LocationGetArgs.model_json_schema()
    description = str(schema.get("description", "")).lower()
    assert "no arguments" in description
    assert "current location" in description
    assert 'prefer="auto"' in description
    assert 'max_privacy="city"' in description

    properties = schema.get("properties", {})
    assert isinstance(properties, dict)
    prefer_description = str(properties["prefer"].get("description", "")).lower()
    privacy_description = str(properties["max_privacy"].get("description", "")).lower()
    assert "current-location" in prefer_description
    assert '"auto"' in prefer_description
    assert "privacy cap" in privacy_description
    assert '"city"' in privacy_description


def test_location_set_default_requires_city() -> None:
    with pytest.raises(ValidationError):
        LocationSetDefaultArgs()


def test_location_set_default_accepts_minimal_payload() -> None:
    args = LocationSetDefaultArgs(city="San Francisco")
    assert args.city == "San Francisco"
    assert args.privacy_level == "city"


def test_location_get_ip_has_no_fields() -> None:
    args = LocationGetIPArgs()
    assert args.max_privacy == "city"
    assert args.refresh is False
