from __future__ import annotations

import importlib


def test_tool_telemetry_events_import_without_circular_dependency() -> None:
    module = importlib.import_module("openminion.modules.tool.diagnostics.events")

    assert hasattr(module, "emit_tool_invoke_operation_for_context")


def test_context_package_exports_are_lazy_but_stable() -> None:
    context_pkg = importlib.import_module("openminion.modules.context")

    assert context_pkg.ContextCtlService.__name__ == "ContextCtlService"
    assert context_pkg.ContextPackBuilder.__name__ == "ContextPackBuilder"


def test_services_package_exports_are_lazy_but_stable() -> None:
    services_pkg = importlib.import_module("openminion.services")

    assert services_pkg.AgentService.__name__ == "AgentService"
    assert services_pkg.GatewayService.__name__ == "GatewayService"
