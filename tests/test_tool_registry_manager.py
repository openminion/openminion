from __future__ import annotations

import pytest

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.manager import (
    ToolRegistryManager,
)
from openminion.modules.tool.bootstrap import (
    _emit_contract_drift_report,
    build_runtime_bootstrap,
)


def _get_bootstrap_manager() -> ToolRegistryManager:
    bootstrap = build_runtime_bootstrap(
        config=None, workspace_root=None, run_root=None, strict=False
    )
    return bootstrap.manager


def test_default_manager_resolves_model_binding_and_candidates() -> None:
    manager = _get_bootstrap_manager()
    assert manager.normalize_raw_name("functions.web.search") == "web.search"
    assert manager.resolve_binding("web.search") == "runtime.web.search"
    candidates = manager.runtime_candidates("runtime.web.search")
    if candidates:
        assert candidates[0] == "search.dispatch"
        assert "search.tavily.search" in candidates
        assert "search.serpapi.search" in candidates
        assert "search.firecrawl.search" in candidates
        assert "search.serper.search" in candidates
        assert "search.tinyfish.search" in candidates


def test_default_manager_normalizes_unique_runtime_candidate_name() -> None:
    manager = _get_bootstrap_manager()
    assert manager.normalize_raw_name("file.read") == "file.read"
    assert manager.resolve_binding("file.read") == "runtime.file.read"


def test_model_provider_specs_only_exposes_available_runtime_tools() -> None:
    manager = _get_bootstrap_manager()
    specs = manager.model_provider_specs({"file.read", "search.tavily.search"})
    names = {spec.name for spec in specs}
    assert "file.read" in names
    if manager.runtime_candidates("runtime.web.search"):
        assert "web.search" in names
    else:
        assert "web.search" not in names
    assert "file.write" not in names


def test_schema_for_file_read_includes_path_property() -> None:
    manager = _get_bootstrap_manager()
    schema = manager.schema_for("file.read")
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    assert "path" in properties


def test_model_provider_specs_expose_time_location_and_timezone_contract() -> None:
    manager = _get_bootstrap_manager()

    specs = manager.model_provider_specs({"time.now"})
    time_spec = next(spec for spec in specs if spec.name == "time")

    properties = (
        time_spec.parameters.get("properties", {})
        if isinstance(time_spec.parameters, dict)
        else {}
    )
    assert "timezone" in properties
    assert "location" in properties
    assert "named place" in time_spec.description.lower()


def test_manager_exposes_model_to_runtime_maps() -> None:
    manager = _get_bootstrap_manager()
    binding_map = manager.model_to_runtime_binding_map()
    assert binding_map["web.search"] == "runtime.web.search"
    assert binding_map["weather"] == "runtime.weather.current"

    runtime_tool_map = manager.model_to_runtime_tool_map(
        {
            "search.tavily.search",
            "search.serpapi.search",
            "search.firecrawl.search",
            "search.serper.search",
            "search.tinyfish.search",
            "weather",
            "time.now",
        }
    )
    if manager.runtime_candidates("runtime.web.search"):
        assert runtime_tool_map["web.search"] == "search.tavily.search"
    else:
        assert "web.search" not in runtime_tool_map
    # runtime candidates are now ("weather",) only; "weather" resolves to "weather"
    assert runtime_tool_map["weather"] == "weather"
    assert runtime_tool_map["time"] == "time.now"

    dispatch_map = manager.model_runtime_dispatch_map(
        {
            "search.tavily.search",
            "search.serpapi.search",
            "search.firecrawl.search",
            "search.serper.search",
            "search.tinyfish.search",
            "weather",
            "time.now",
        }
    )
    assert dispatch_map["web.search"]["runtime_binding_id"] == "runtime.web.search"
    if manager.runtime_candidates("runtime.web.search"):
        assert dispatch_map["web.search"]["runtime_tool_name"] == "search.tavily.search"
        assert (
            "search.serpapi.search" in dispatch_map["web.search"]["runtime_candidates"]
        )
        assert (
            "search.firecrawl.search"
            in dispatch_map["web.search"]["runtime_candidates"]
        )
        assert (
            "search.serper.search" in dispatch_map["web.search"]["runtime_candidates"]
        )
        assert (
            "search.tinyfish.search" in dispatch_map["web.search"]["runtime_candidates"]
        )
    else:
        assert dispatch_map["web.search"]["runtime_tool_name"] == ""
    assert dispatch_map["weather"]["runtime_tool_name"] == "weather"


def test_manager_weather_runtime_candidates_is_weather_only() -> None:
    manager = _get_bootstrap_manager()
    runtime_tool_map = manager.model_to_runtime_tool_map({"weather"})
    assert runtime_tool_map["weather"] == "weather"

    dispatch_map = manager.model_runtime_dispatch_map({"weather"})
    assert dispatch_map["weather"]["runtime_binding_id"] == "runtime.weather.current"
    assert dispatch_map["weather"]["runtime_tool_name"] == "weather"
    assert dispatch_map["weather"]["runtime_candidates"] == ["weather"]


def test_manager_normalizes_tool_search_alias_to_tool_list() -> None:
    manager = _get_bootstrap_manager()
    assert manager.normalize_model_input_name("tool.search") == "tool.list"
    assert manager.normalize_raw_name("tool.search") == "tool.list"
    assert manager.resolve_binding("tool.search") == "runtime.tool.list"


def test_manager_normalizes_host_status_aliases_to_host_metrics() -> None:
    manager = _get_bootstrap_manager()
    assert manager.normalize_model_input_name("system.status") == "host.metrics"
    assert manager.normalize_model_input_name("host.status") == "host.metrics"
    assert manager.normalize_raw_name("system.status") == "host.metrics"
    assert manager.resolve_binding("host.metrics") == "runtime.host.metrics"
    assert manager.runtime_candidates("runtime.host.metrics") == ("host.metrics",)


def test_manager_exposes_canonical_tool_catalog_rows() -> None:
    manager = _get_bootstrap_manager()
    catalog = dict(manager.model_tool_catalog())
    assert "tool.list" in catalog
    assert "tool.search" not in catalog
    assert "file.search" in catalog
    assert "file.edit" in catalog


def test_default_registry_bootstrap_has_no_unresolved_runtime_bindings() -> None:
    from openminion.modules.tool import build_default_tool_registry

    registry = build_default_tool_registry()
    snapshot = registry.registration_debug_snapshot()
    unresolved = snapshot.get("manager", {}).get("unresolved_runtime_binding_ids", [])
    assert unresolved == [], f"unresolved runtime binding IDs: {unresolved}"


def test_manager_compile_rejects_duplicate_model_tool_ids() -> None:
    manager = ToolRegistryManager()
    manager.register_manifest(
        ToolBindingManifest(
            module_id="mod.one",
            model_tools=(
                ModelToolDef(
                    model_tool_id="web.search",
                    description="Search one",
                    parameters={},
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id="runtime.web.search",
                    model_tool_id="web.search",
                    runtime_candidates=("search.tavily.search",),
                ),
            ),
        )
    )
    manager.register_manifest(
        ToolBindingManifest(
            module_id="mod.two",
            model_tools=(
                ModelToolDef(
                    model_tool_id="web.search",
                    description="Duplicate",
                    parameters={},
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id="runtime.web.fetch",
                    model_tool_id="web.search",
                    runtime_candidates=("fetch.get",),
                ),
            ),
        )
    )
    with pytest.raises(ToolRuntimeError, match="Duplicate model_tool_id"):
        manager.compile()


def test_register_manifest_rejects_non_canonical_model_tool_id() -> None:
    manager = ToolRegistryManager()
    with pytest.raises(ToolRuntimeError, match="non-canonical model_tool_id"):
        manager.register_manifest(
            ToolBindingManifest(
                module_id="mod.noncanonical.model",
                model_tools=(
                    ModelToolDef(
                        model_tool_id="custom.echo",
                        description="Custom",
                        parameters={},
                    ),
                ),
                runtime_bindings=(
                    RuntimeBindingDef(
                        runtime_binding_id="runtime.web.search",
                        model_tool_id="custom.echo",
                        runtime_candidates=("search.tavily.search",),
                    ),
                ),
            )
        )


def test_register_manifest_rejects_non_canonical_runtime_binding_id() -> None:
    manager = ToolRegistryManager()
    with pytest.raises(ToolRuntimeError, match="non-canonical runtime_binding_id"):
        manager.register_manifest(
            ToolBindingManifest(
                module_id="mod.noncanonical.binding",
                model_tools=(
                    ModelToolDef(
                        model_tool_id="web.search",
                        description="Search",
                        parameters={},
                    ),
                ),
                runtime_bindings=(
                    RuntimeBindingDef(
                        runtime_binding_id="runtime.custom.echo",
                        model_tool_id="web.search",
                        runtime_candidates=("search.tavily.search",),
                    ),
                ),
            )
        )


def test_contract_drift_report_is_clean_for_bootstrap_manager() -> None:
    manager = _get_bootstrap_manager()
    report = manager.contract_drift_report()
    assert report.has_drift is False
    assert report.model_tool_ids_missing_from_manifests == ()
    assert report.model_tool_ids_missing_from_contracts == ()
    assert report.runtime_binding_ids_missing_from_manifests == ()
    assert report.runtime_binding_ids_missing_from_contracts == ()


def test_contract_drift_report_detects_missing_ids_for_empty_manager() -> None:
    manager = ToolRegistryManager()
    manager.compile()
    report = manager.contract_drift_report()
    assert report.has_drift is True
    assert len(report.model_tool_ids_missing_from_manifests) > 0
    assert len(report.runtime_binding_ids_missing_from_manifests) > 0


def test_emit_contract_drift_report_fails_in_ci_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ToolRegistryManager()
    manager.compile()
    monkeypatch.setenv("CI", "1")
    with pytest.raises(ToolRuntimeError, match="Tool contract drift detected"):
        _emit_contract_drift_report(manager)
