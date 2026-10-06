from __future__ import annotations

from typing import Any

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime.manager import ToolRegistryManager


def _create_fake_toolspec(name: str, fail_with: str | None = None) -> ToolSpec:
    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        if fail_with:
            return {"ok": False, "error": fail_with, "content": ""}
        return {"ok": True, "content": "success"}

    return ToolSpec(
        name=name,
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )


def _policy_registry(monkeypatch, *, fail_with: str) -> ToolRegistry:
    from openminion.modules.tool import dispatch as runtime_dispatch

    manager = ToolRegistryManager()
    manager.register_module_manifest(
        ToolBindingManifest(
            module_id="test.search",
            model_tools=(ModelToolDef("web.search", "Search", {}),),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id="runtime.web.search",
                    model_tool_id="web.search",
                    runtime_candidates=(
                        "search.tavily.search",
                        "search.fallback",
                    ),
                ),
            ),
        ),
        source_module="test.search",
    )
    monkeypatch.setattr(runtime_dispatch, "_REGISTRY_MANAGER", manager)

    registry = ToolRegistry()
    registry._tools["search.tavily.search"] = _create_fake_toolspec(
        "search.tavily.search", fail_with=fail_with
    )
    registry._tools["search.fallback"] = _create_fake_toolspec("search.fallback")
    return registry


def test_execute_calls_uses_policy_manager_fallback_tokens(monkeypatch) -> None:
    registry = _policy_registry(monkeypatch, fail_with="timeout from upstream")

    context = ToolExecutionContext(
        channel="test",
        target="test",
        metadata={
            "runtime_binding_policies": {
                "runtime.web.search": {
                    "primary": "search.tavily.search",
                    "fallback_tools": ["search.fallback"],
                },
                "runtime_fallback_on": ["timeout", "unavailable"],
                "runtime_no_fallback_on": ["policy_denied"],
            }
        },
    )

    call = ProviderToolCall(
        name="web.search", arguments={"query": "test"}, id="call1", source="test"
    )
    batch = registry.execute_calls([call], context=context)

    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.ok
    assert result.content == "success"
    assert result.data["runtime_fallback_used"]
    assert result.data["tool_min_scope"] == "READ_ONLY"
    assert result.data["tool_blast_radius"] == "read_only"


def test_execute_calls_respects_denylist_precedence(monkeypatch) -> None:
    registry = _policy_registry(
        monkeypatch,
        fail_with="policy_denied: safety violation",
    )

    context = ToolExecutionContext(
        channel="test",
        target="test",
        metadata={
            "runtime_binding_policies": {
                "runtime.web.search": {
                    "primary": "search.tavily.search",
                    "fallback_tools": ["search.fallback"],
                },
                "runtime_fallback_on": ["timeout", "policy_denied"],
                "runtime_no_fallback_on": ["safety", "policy_denied"],
            }
        },
    )

    call = ProviderToolCall(
        name="web.search", arguments={"query": "test"}, id="call1", source="test"
    )
    batch = registry.execute_calls([call], context=context)

    assert len(batch.results) == 1
    result = batch.results[0]
    assert not result.ok
    assert not result.data["runtime_fallback_used"]


def test_execute_calls_no_fallback_on_auth_errors(monkeypatch) -> None:
    registry = _policy_registry(
        monkeypatch,
        fail_with="auth failed: invalid credentials",
    )

    context = ToolExecutionContext(
        channel="test",
        target="test",
        metadata={
            "runtime_binding_policies": {
                "runtime.web.search": {
                    "primary": "search.tavily.search",
                    "fallback_tools": ["search.fallback"],
                },
                "runtime_fallback_on": ["timeout", "unavailable", "auth"],
                "runtime_no_fallback_on": ["auth", "permission"],
            }
        },
    )

    call = ProviderToolCall(
        name="web.search", arguments={"query": "test"}, id="call1", source="test"
    )
    batch = registry.execute_calls([call], context=context)

    assert len(batch.results) == 1
    result = batch.results[0]
    assert not result.ok
    assert not result.data["runtime_fallback_used"]


def test_execute_calls_custom_fallback_tokens(monkeypatch) -> None:
    registry = _policy_registry(
        monkeypatch,
        fail_with="custom_transient_error: try again",
    )

    context = ToolExecutionContext(
        channel="test",
        target="test",
        metadata={
            "runtime_binding_policies": {
                "runtime.web.search": {
                    "primary": "search.tavily.search",
                    "fallback_tools": ["search.fallback"],
                },
                "runtime_fallback_on": ["custom_transient_error", "timeout"],
                "runtime_no_fallback_on": ["permanent_failure"],
            }
        },
    )

    call = ProviderToolCall(
        name="web.search", arguments={"query": "test"}, id="call1", source="test"
    )
    batch = registry.execute_calls([call], context=context)

    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.ok
    assert result.data["runtime_fallback_used"]


def test_execute_calls_permanent_failure_no_fallback(monkeypatch) -> None:
    registry = _policy_registry(
        monkeypatch,
        fail_with="permanent_failure: invalid input",
    )

    context = ToolExecutionContext(
        channel="test",
        target="test",
        metadata={
            "runtime_binding_policies": {
                "runtime.web.search": {
                    "primary": "search.tavily.search",
                    "fallback_tools": ["search.fallback"],
                },
                "runtime_fallback_on": ["timeout", "unavailable"],
                "runtime_no_fallback_on": ["permanent_failure", "invalid_input"],
            }
        },
    )

    call = ProviderToolCall(
        name="web.search", arguments={"query": "test"}, id="call1", source="test"
    )
    batch = registry.execute_calls([call], context=context)

    assert len(batch.results) == 1
    result = batch.results[0]
    assert not result.ok
    assert not result.data["runtime_fallback_used"]


def test_execute_calls_without_policy_metadata_uses_defaults() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()
    registry = bootstrap.registry

    primary = _create_fake_toolspec(
        "search.dispatch", fail_with="timeout from upstream"
    )
    registry._tools["search.dispatch"] = primary

    context = ToolExecutionContext(channel="test", target="test", metadata={})

    call = ProviderToolCall(
        name="web.search", arguments={"query": "test"}, id="call1", source="test"
    )
    batch = registry.execute_calls([call], context=context)

    assert len(batch.results) == 1
    result = batch.results[0]
    assert not result.ok
    assert "timeout" in result.error
