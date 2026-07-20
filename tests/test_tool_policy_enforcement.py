from __future__ import annotations

from typing import Any

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolSpec


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


def test_execute_calls_uses_policy_manager_fallback_tokens() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()
    registry = bootstrap.registry

    primary = _create_fake_toolspec(
        "search.tavily.search", fail_with="timeout from upstream"
    )
    fallback = _create_fake_toolspec("search.fallback")
    registry._tools["search.tavily.search"] = primary
    registry._tools["search.fallback"] = fallback

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


def test_execute_calls_respects_denylist_precedence() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()
    registry = bootstrap.registry

    primary = _create_fake_toolspec(
        "search.tavily.search", fail_with="policy_denied: safety violation"
    )
    fallback = _create_fake_toolspec("search.fallback")
    registry._tools["search.tavily.search"] = primary
    registry._tools["search.fallback"] = fallback

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


def test_execute_calls_no_fallback_on_auth_errors() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()
    registry = bootstrap.registry

    primary = _create_fake_toolspec(
        "search.tavily.search", fail_with="auth failed: invalid credentials"
    )
    fallback = _create_fake_toolspec("search.fallback")
    registry._tools["search.tavily.search"] = primary
    registry._tools["search.fallback"] = fallback

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


def test_execute_calls_custom_fallback_tokens() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()
    registry = bootstrap.registry

    primary = _create_fake_toolspec(
        "search.tavily.search", fail_with="custom_transient_error: try again"
    )
    fallback = _create_fake_toolspec("search.fallback")
    registry._tools["search.tavily.search"] = primary
    registry._tools["search.fallback"] = fallback

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


def test_execute_calls_permanent_failure_no_fallback() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()
    registry = bootstrap.registry

    primary = _create_fake_toolspec(
        "search.tavily.search", fail_with="permanent_failure: invalid input"
    )
    fallback = _create_fake_toolspec("search.fallback")
    registry._tools["search.tavily.search"] = primary
    registry._tools["search.fallback"] = fallback

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
