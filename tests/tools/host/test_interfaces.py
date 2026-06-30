from __future__ import annotations

import openminion.tools.host as host_pkg

from openminion.tools.host.interfaces import (
    HOST_PLUGIN_INTERFACE_VERSION,
    TOOL_HOST_METRICS,
)


def test_host_interface_version_is_v1() -> None:
    assert HOST_PLUGIN_INTERFACE_VERSION == "v1"


def test_host_tool_name_constant() -> None:
    assert TOOL_HOST_METRICS == "host.metrics"


def test_host_package_exports_register() -> None:
    assert callable(host_pkg.register)
