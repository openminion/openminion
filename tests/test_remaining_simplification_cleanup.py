from __future__ import annotations

import importlib

import pytest

import openminion.cli.parser.contracts as cli_contracts
from openminion.api.runtime import APIRuntime
from openminion.api.server import (
    _OpenMinionAPIHandler,
    get_api_metrics_consistency_stamp,
    get_api_metrics_snapshot,
    reset_api_metrics,
)


def test_supported_api_runtime_and_server_imports_still_exist() -> None:
    assert APIRuntime is not None
    assert _OpenMinionAPIHandler is not None
    assert callable(get_api_metrics_snapshot)
    assert callable(get_api_metrics_consistency_stamp)
    assert callable(reset_api_metrics)


@pytest.mark.parametrize(
    "module_name",
    [
        "openminion.api.core.runtime",
        "openminion.api.server.handler",
        "openminion.api.server.metrics",
        "openminion.services.health.constants",
    ],
)
def test_removed_simplification_modules_are_no_longer_importable(
    module_name: str,
) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_cli_contracts_keep_chat_runtime_api_only() -> None:
    assert hasattr(cli_contracts, "AgentRuntimeAPI")
    assert hasattr(cli_contracts, "ChatRuntimeAPI")
    assert not hasattr(cli_contracts, "TUIRuntimeAPI")


def test_removed_package_facades_and_alias_exports_stay_absent() -> None:
    health_pkg = importlib.import_module("openminion.services.health")
    api_core_pkg = importlib.import_module("openminion.api.core")
    session_interfaces = importlib.import_module(
        "openminion.modules.session.interfaces"
    )
    api_streaming = importlib.import_module("openminion.api.server.streaming")

    assert not hasattr(health_pkg, "collect_health_snapshot")
    assert not hasattr(health_pkg, "_evaluate_supervision_decision")
    assert not hasattr(api_core_pkg, "TurnSubmission")
    assert not hasattr(api_core_pkg, "open_turn_submission")
    assert not hasattr(session_interfaces, "CronRepository")
    assert not hasattr(api_streaming, "OpenMinionStreamHandler")
