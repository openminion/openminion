from __future__ import annotations

import openminion.tools.ip as ip_pkg

from openminion.tools.ip.interfaces import (
    IP_PLUGIN_INTERFACE_VERSION,
    TOOL_IP_LOCAL,
    TOOL_IP_PUBLIC,
)


def test_ip_interface_version_is_v1() -> None:
    assert IP_PLUGIN_INTERFACE_VERSION == "v1"


def test_ip_tool_name_constants() -> None:
    assert TOOL_IP_PUBLIC == "ip.public"
    assert TOOL_IP_LOCAL == "ip.local"


def test_ip_package_exports_register_provider() -> None:
    assert callable(ip_pkg.register_provider)
