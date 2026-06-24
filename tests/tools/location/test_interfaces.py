from __future__ import annotations

from openminion.tools.location.interfaces import (
    LOCATION_PLUGIN_INTERFACE_VERSION,
    LOCATION_SOURCE_VALUES,
)


def test_interface_version_marker() -> None:
    assert LOCATION_PLUGIN_INTERFACE_VERSION == "v1"


def test_source_enum_values() -> None:
    assert LOCATION_SOURCE_VALUES == (
        "session.override",
        "identity.default",
        "ip.geo",
        "none",
    )
