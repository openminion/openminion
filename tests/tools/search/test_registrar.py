from __future__ import annotations

import importlib
from types import SimpleNamespace

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
    assert "search.dispatch" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.tavily.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.brave.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.serpapi.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.firecrawl.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.serper.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.tinyfish.search" in manifest.runtime_bindings[0].runtime_candidates


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


def test_search_registrar_registers_when_provider_env_present() -> None:
    registrar = _registrar_cls()()

    manifest = registrar.get_manifest(
        _ctx({"DASHSCOPE_API_KEY": "x", "TAVILY_API_KEY": "tvly-test"})
    )

    assert manifest is not None
    assert "search.dispatch" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.firecrawl.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.serper.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.tinyfish.search" in manifest.runtime_bindings[0].runtime_candidates


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
    assert "search.dispatch" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.firecrawl.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.serper.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.tinyfish.search" in manifest.runtime_bindings[0].runtime_candidates


def test_search_registrar_keeps_runtime_candidates_without_config() -> None:
    registrar = _registrar_cls()()

    manifest = registrar.get_manifest(None)

    assert manifest is not None
    assert "search.dispatch" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.serpapi.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.firecrawl.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.serper.search" in manifest.runtime_bindings[0].runtime_candidates
    assert "search.tinyfish.search" in manifest.runtime_bindings[0].runtime_candidates


def test_search_plugin_import_avoids_search_tavily_cycle() -> None:
    module = importlib.import_module("openminion.tools.search.plugin")

    assert module is not None


def test_search_args_provider_description_mentions_serper_and_tinyfish() -> None:
    schemas = importlib.import_module("openminion.tools.search.schemas")

    field = schemas.SearchArgs.model_fields["provider"]
    assert field.description is not None
    assert "firecrawl" in field.description
    assert "serper" in field.description
    assert "tinyfish" in field.description
