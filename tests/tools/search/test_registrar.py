from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from openminion.base.config.env import resolve_environment_config
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry


def _registrar_cls():
    module = importlib.import_module("openminion.tools.search.registrar")
    return module.SearchRegistrar


def _ctx(runtime_env: dict[str, str] | None) -> ToolRegisterContext:
    config = SimpleNamespace(runtime=SimpleNamespace(env=runtime_env or {}))
    return ToolRegisterContext(module_id="search", config=config)


def test_search_registrar_keeps_manifest_without_provider_env() -> None:
    registrar = _registrar_cls()()

    manifest = registrar.get_manifest(_ctx({"DASHSCOPE_API_KEY": "x"}))

    assert manifest is not None
    assert manifest.runtime_bindings[0].runtime_candidates == ("search.dispatch",)


def test_search_registrar_registers_runtime_tools_without_provider_env() -> None:
    registrar = _registrar_cls()()
    registry = ToolRegistry()

    registrar.register(registry, _ctx({"DASHSCOPE_API_KEY": "x"}))

    names = set(registry.list().keys())
    assert "search.dispatch" in names
    assert "search.tavily.search" in names
    assert "search.brave.search" in names
    assert "search.serpapi.search" in names
    assert "search.firecrawl.search" in names
    assert "search.serper.search" in names
    assert "search.tinyfish.search" in names
    assert "search.duckduckgo.search" in names


def test_search_registrar_registers_when_provider_env_present() -> None:
    registrar = _registrar_cls()()

    manifest = registrar.get_manifest(
        _ctx({"DASHSCOPE_API_KEY": "x", "TAVILY_API_KEY": "tvly-test"})
    )

    assert manifest is not None
    assert manifest.runtime_bindings[0].runtime_candidates == ("search.dispatch",)


def test_search_registrar_registers_with_environment_config_runtime_env() -> None:
    registrar = _registrar_cls()()
    runtime_env = resolve_environment_config(
        env={"DASHSCOPE_API_KEY": "x", "TAVILY_API_KEY": "tvly-test"}
    )
    ctx = ToolRegisterContext(
        module_id="search",
        config=SimpleNamespace(runtime=SimpleNamespace(env=runtime_env)),
    )

    manifest = registrar.get_manifest(ctx)

    assert manifest is not None
    assert manifest.runtime_bindings[0].runtime_candidates == ("search.dispatch",)


def test_search_registrar_keeps_runtime_candidates_without_config() -> None:
    registrar = _registrar_cls()()

    manifest = registrar.get_manifest(None)

    assert manifest is not None
    assert manifest.runtime_bindings[0].runtime_candidates == ("search.dispatch",)


def test_search_plugin_import_avoids_search_tavily_cycle() -> None:
    module = importlib.import_module("openminion.tools.search.plugin")

    assert module is not None


def test_search_args_provider_description_mentions_all_recent_providers() -> None:
    schemas = importlib.import_module("openminion.tools.search.schemas")

    field = schemas.SearchArgs.model_fields["provider"]
    assert field.description is not None
    assert "firecrawl" in field.description
    assert "serper" in field.description
    assert "tinyfish" in field.description
    assert "duckduckgo" in field.description


def test_search_args_keep_credentials_out_of_model_visible_schema() -> None:
    schemas = importlib.import_module("openminion.tools.search.schemas")

    assert "api_key" not in schemas.SearchArgs.model_json_schema()["properties"]
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        schemas.SearchArgs.model_validate({"query": "cats", "api_key": "secret"})
