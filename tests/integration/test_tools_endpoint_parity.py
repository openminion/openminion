from __future__ import annotations

from pathlib import Path

from openminion.api.config import resolve_api_runtime
from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.tools import handle_request as tools_handle_request
from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.config import resolve_cli_tool_provider_specs_and_dispatch_map
from openminion.services.tool.exposure import get_visible_tool_specs_and_dispatch_map
from tests._csc_fixtures import _csc_install_default_agent


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path


def _tools_route_names(runtime: APIRuntime, config_path: Path) -> list[str]:
    ctx = APIRouteContext(
        config_path=str(config_path),
        runtime=runtime,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="mfrc-05-test",
    )
    result = tools_handle_request(
        ctx,
        method_name="GET",
        path="/v1/tools",
        body=None,
        query=None,
    )
    assert result is not None, "/v1/tools must produce a response"
    assert result.status == 200
    assert result.payload.get("ok")
    return sorted(item["name"] for item in result.payload.get("tools", []))


def test_v1_tools_route_and_runtime_report_agree(tmp_path: Path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        assert _tools_route_names(runtime, config_path) == sorted(
            item["name"] for item in runtime.tool_inventory_report()
        )
    finally:
        runtime.close()


def test_cli_inproc_fallback_matches_v1_tools(tmp_path: Path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        specs, _dispatch = resolve_cli_tool_provider_specs_and_dispatch_map(
            runtime.tools
        )
        cli_names = sorted(spec.name for spec in specs)
        assert cli_names == _tools_route_names(runtime, config_path)
    finally:
        runtime.close()


def test_canonical_function_is_shared_owner(tmp_path: Path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        canonical_specs, _ = get_visible_tool_specs_and_dispatch_map(runtime.tools)
        canonical_names = sorted(spec.name for spec in canonical_specs)
        cli_specs, _ = resolve_cli_tool_provider_specs_and_dispatch_map(runtime.tools)
        cli_names = sorted(spec.name for spec in cli_specs)
        report_names = sorted(item["name"] for item in runtime.tool_inventory_report())
        assert canonical_names == cli_names
        assert canonical_names == report_names
    finally:
        runtime.close()


def test_tool_catalog_is_non_empty_under_module_mode(tmp_path: Path) -> None:
    config_path = _write_echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        assert len(runtime.tool_inventory_report()) > 0
    finally:
        runtime.close()


def test_resolve_api_runtime_helper_unused() -> None:
    assert callable(resolve_api_runtime)
