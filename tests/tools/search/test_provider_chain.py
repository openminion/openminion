from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.search import plugin as search_plugin


class _ContextAwareBraveProvider:
    provider_id = "brave"
    display_name = "Brave"

    def __init__(self, *, healthy_without_ctx: bool = False) -> None:
        self._healthy_without_ctx = healthy_without_ctx
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args
        self.search_calls += 1
        return {
            "provider": "brave",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "Brave Result",
                    "url": "https://example.com/brave",
                    "description": "Brave result",
                }
            ],
        }

    def healthcheck(self, ctx=None) -> bool:
        if self._healthy_without_ctx:
            return True
        env = getattr(ctx, "env", {}) if ctx is not None else {}
        return bool(getattr(env, "get", lambda *_args, **_kwargs: "")("BRAVE_API_KEY"))


class _UnhealthyButWorkingProvider:
    provider_id = "brave"
    display_name = "Brave"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args, ctx
        self.search_calls += 1
        return {
            "provider": "brave",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "Brave Result",
                    "url": "https://example.com/brave",
                    "description": "Brave result",
                }
            ],
        }

    def healthcheck(self, ctx=None) -> bool:
        del ctx
        return False


class _LegacyHealthyTavilyProvider:
    provider_id = "tavily"
    display_name = "Tavily"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args, ctx
        self.search_calls += 1
        return {
            "provider": "tavily",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "Tavily Result",
                    "url": "https://example.com/tavily",
                    "description": "Tavily result",
                }
            ],
        }

    def healthcheck(self) -> bool:
        return True


class _LegacyHealthySerpApiProvider:
    provider_id = "serpapi"
    display_name = "SerpApi"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args, ctx
        self.search_calls += 1
        return {
            "provider": "serpapi",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "SerpApi Result",
                    "url": "https://example.com/serpapi",
                    "description": "SerpApi result",
                }
            ],
        }

    def healthcheck(self) -> bool:
        return True


class _LegacyHealthyFirecrawlProvider:
    provider_id = "firecrawl"
    display_name = "Firecrawl"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args, ctx
        self.search_calls += 1
        return {
            "provider": "firecrawl",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "Firecrawl Result",
                    "url": "https://example.com/firecrawl",
                    "description": "Firecrawl result",
                }
            ],
        }

    def healthcheck(self) -> bool:
        return True


class _LegacyHealthySerperProvider:
    provider_id = "serper"
    display_name = "Serper"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args, ctx
        self.search_calls += 1
        return {
            "provider": "serper",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "Serper Result",
                    "url": "https://example.com/serper",
                    "description": "Serper result",
                }
            ],
        }

    def healthcheck(self) -> bool:
        return True


class _LegacyHealthyTinyFishProvider:
    provider_id = "tinyfish"
    display_name = "TinyFish"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del max_results, args, ctx
        self.search_calls += 1
        return {
            "provider": "tinyfish",
            "query": {"original": query, "more_results_available": False},
            "results": [
                {
                    "title": "TinyFish Result",
                    "url": "https://example.com/tinyfish",
                    "description": "TinyFish result",
                }
            ],
        }

    def healthcheck(self) -> bool:
        return True


class _LegacyUnhealthyProvider:
    provider_id = "brave"
    display_name = "Brave"

    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, query, *, max_results, args, ctx):
        del query, max_results, args, ctx
        self.search_calls += 1
        raise AssertionError("unhealthy provider should be filtered before search")

    def healthcheck(self) -> bool:
        return False


def _runtime_ctx(
    *,
    env: dict[str, str] | None = None,
    runtime_tools: dict[str, object] | None = None,
    runtime_binding_policies: dict[str, object] | None = None,
):
    context_metadata: dict[str, object] = {}
    if runtime_tools is not None:
        context_metadata["runtime_tools"] = dict(runtime_tools)
    if runtime_binding_policies is not None:
        context_metadata["runtime_binding_policies"] = dict(runtime_binding_policies)
    policy = None
    if context_metadata:
        policy = SimpleNamespace(raw={"context_metadata": context_metadata})
    return SimpleNamespace(policy=policy, env=dict(env or {}))


def _audit_runtime_ctx(
    tmp_path: Path, *, runtime_tools: dict[str, object] | None = None
):
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    raw: dict[str, object] = {
        "workspace_root": str(workspace),
        "paths": {
            "read_allow": [str(workspace)],
            "write_allow": [str(workspace)],
            "deny": [],
        },
        "commands": {"mode": "allowlist", "allow": ["echo"]},
    }
    if runtime_tools is not None:
        raw["context_metadata"] = {"runtime_tools": runtime_tools}
    return RuntimeContext(
        policy=Policy(raw=raw),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def _reset_providers() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def setup_function() -> None:
    _reset_providers()


def teardown_function() -> None:
    _reset_providers()


def test_explicit_provider_uses_context_aware_healthcheck_and_executes_first() -> None:
    brave = _ContextAwareBraveProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "brave", "max_results": 5},
        _runtime_ctx(env={"BRAVE_API_KEY": "ctx-only-key"}),
    )

    assert result["ok"] is True
    assert result["source"] == "brave"
    assert result["data"]["provider"] == "brave"
    assert result["data"]["retrieved_at"]
    assert brave.search_calls == 1
    assert tavily.search_calls == 0


def test_explicit_provider_is_preserved_even_when_healthcheck_is_false() -> None:
    brave = _UnhealthyButWorkingProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "brave", "max_results": 5},
        _runtime_ctx(),
    )

    assert result["ok"] is True
    assert result["source"] == "brave"
    assert "provider 'brave' reported unhealthy" in result["data"]["warnings"]
    assert brave.search_calls == 1
    assert tavily.search_calls == 0


def test_auto_provider_filters_unhealthy_candidates_but_keeps_healthy_fallback() -> (
    None
):
    brave = _LegacyUnhealthyProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "auto", "max_results": 5},
        _runtime_ctx(),
    )

    assert result["ok"] is True
    assert result["source"] == "tavily"
    assert "provider 'brave' reported unhealthy" in result["data"]["warnings"]
    assert brave.search_calls == 0
    assert tavily.search_calls == 1


def test_runtime_tools_provider_order_overrides_legacy_policy_and_env() -> None:
    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "auto", "max_results": 5},
        _runtime_ctx(
            env={"OPENMINION_WEB_SEARCH_PROVIDER": "brave"},
            runtime_tools={
                "search": {
                    "enabled_providers": ["tavily", "brave"],
                    "default_provider": "tavily",
                    "provider_order": ["tavily", "brave"],
                    "allow_fallback": True,
                }
            },
            runtime_binding_policies={
                "runtime.web.search": {
                    "primary": "brave",
                    "fallback_tools": ["tavily"],
                }
            },
        ),
    )

    assert result["ok"] is True
    assert result["source"] == "tavily"
    assert tavily.search_calls == 1
    assert brave.search_calls == 0


def test_serpapi_alias_selects_registered_serpapi_provider() -> None:
    serpapi = _LegacyHealthySerpApiProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(tavily)
    search_plugin.register_provider(serpapi)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "search.serpapi.search", "max_results": 5},
        _runtime_ctx(),
    )

    assert result["ok"] is True
    assert result["source"] == "serpapi"
    assert result["data"]["provider"] == "serpapi"
    assert serpapi.search_calls == 1
    assert tavily.search_calls == 0


def test_forced_serpapi_handler_delegates_through_shared_search_path() -> None:
    serpapi = _LegacyHealthySerpApiProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(tavily)
    search_plugin.register_provider(serpapi)

    result = search_plugin._handle_web_search_serpapi(
        {"query": "cats", "max_results": 5},
        _runtime_ctx(),
    )

    assert result["ok"] is True
    assert result["source"] == "serpapi"
    assert result["data"]["provider"] == "serpapi"
    assert serpapi.search_calls == 1
    assert tavily.search_calls == 0


def test_provider_registration_order_can_append_serpapi_after_existing_providers() -> (
    None
):
    tavily = _LegacyHealthyTavilyProvider()
    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    serpapi = _LegacyHealthySerpApiProvider()

    search_plugin.register_provider(tavily)
    search_plugin.register_provider(brave)
    search_plugin.register_provider(serpapi)

    assert search_plugin.list_provider_ids() == ("tavily", "brave", "serpapi")


def test_tinyfish_alias_and_forced_wrapper_route_through_shared_search() -> None:
    tinyfish = _LegacyHealthyTinyFishProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(tavily)
    search_plugin.register_provider(tinyfish)

    alias_result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "search.tinyfish.search", "max_results": 5},
        _runtime_ctx(),
    )

    forced_result = search_plugin._handle_web_search_tinyfish(
        {"query": "cats", "max_results": 5},
        _runtime_ctx(),
    )

    assert alias_result["ok"] is True
    assert alias_result["source"] == "tinyfish"
    assert alias_result["data"]["provider"] == "tinyfish"
    assert forced_result["ok"] is True
    assert forced_result["source"] == "tinyfish"
    assert tinyfish.search_calls == 2
    assert tavily.search_calls == 0


def test_provider_registration_order_can_append_tinyfish_after_serper() -> None:
    tavily = _LegacyHealthyTavilyProvider()
    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    serpapi = _LegacyHealthySerpApiProvider()
    firecrawl = _LegacyHealthyFirecrawlProvider()
    serper = _LegacyHealthySerperProvider()
    tinyfish = _LegacyHealthyTinyFishProvider()

    search_plugin.register_provider(tavily)
    search_plugin.register_provider(brave)
    search_plugin.register_provider(serpapi)
    search_plugin.register_provider(firecrawl)
    search_plugin.register_provider(serper)
    search_plugin.register_provider(tinyfish)

    assert search_plugin.list_provider_ids() == (
        "tavily",
        "brave",
        "serpapi",
        "firecrawl",
        "serper",
        "tinyfish",
    )


def test_runtime_tools_allow_fallback_false_stops_after_first_candidate() -> None:
    brave = _UnhealthyButWorkingProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "auto", "max_results": 5},
        _runtime_ctx(
            runtime_tools={
                "search": {
                    "enabled_providers": ["brave", "tavily"],
                    "default_provider": "brave",
                    "provider_order": ["brave", "tavily"],
                    "allow_fallback": False,
                }
            }
        ),
    )

    assert result["ok"] is True
    assert result["source"] == "brave"
    assert "provider 'brave' reported unhealthy" in result["data"]["warnings"]
    assert brave.search_calls == 1
    assert tavily.search_calls == 0


def test_firecrawl_provider_alias_and_forced_wrapper_route_through_shared_search() -> (
    None
):
    firecrawl = _LegacyHealthyFirecrawlProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(firecrawl)
    search_plugin.register_provider(tavily)

    assert (
        search_plugin._provider_pref_from_token("search.firecrawl.search")
        == "firecrawl"
    )
    assert search_plugin._provider_pref_from_token("firecrawl") == "firecrawl"

    result = search_plugin._handle_web_search_firecrawl(
        {"query": "cats", "max_results": 5},
        _runtime_ctx(),
    )

    assert result["ok"] is True
    assert result["source"] == "firecrawl"
    assert result["data"]["provider"] == "firecrawl"
    assert firecrawl.search_calls == 1
    assert tavily.search_calls == 0


def test_serper_provider_alias_and_forced_wrapper_route_through_shared_search() -> None:
    serper = _LegacyHealthySerperProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(serper)
    search_plugin.register_provider(tavily)

    assert search_plugin._provider_pref_from_token("search.serper.search") == "serper"
    assert search_plugin._provider_pref_from_token("serper") == "serper"

    result = search_plugin._handle_web_search_serper(
        {"query": "cats", "max_results": 5},
        _runtime_ctx(),
    )

    assert result["ok"] is True
    assert result["source"] == "serper"
    assert result["data"]["provider"] == "serper"
    assert serper.search_calls == 1
    assert tavily.search_calls == 0


def test_provider_registration_order_can_append_serper_after_firecrawl() -> None:
    tavily = _LegacyHealthyTavilyProvider()
    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    serpapi = _LegacyHealthySerpApiProvider()
    firecrawl = _LegacyHealthyFirecrawlProvider()
    serper = _LegacyHealthySerperProvider()

    search_plugin.register_provider(tavily)
    search_plugin.register_provider(brave)
    search_plugin.register_provider(serpapi)
    search_plugin.register_provider(firecrawl)
    search_plugin.register_provider(serper)

    assert search_plugin.list_provider_ids() == (
        "tavily",
        "brave",
        "serpapi",
        "firecrawl",
        "serper",
    )


def test_explicit_provider_bypasses_runtime_tools_enabled_provider_filter() -> None:
    brave = _ContextAwareBraveProvider()
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "brave", "max_results": 5},
        _runtime_ctx(
            env={"BRAVE_API_KEY": "ctx-only-key"},
            runtime_tools={
                "search": {
                    "enabled_providers": ["tavily"],
                    "default_provider": "tavily",
                    "provider_order": ["tavily"],
                    "allow_fallback": True,
                }
            },
        ),
    )

    assert result["ok"] is True
    assert result["source"] == "brave"
    assert brave.search_calls == 1
    assert tavily.search_calls == 0


def test_legacy_policy_order_is_used_when_runtime_tools_absent() -> None:
    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "auto", "max_results": 5},
        _runtime_ctx(
            runtime_binding_policies={
                "runtime.web.search": {
                    "primary": "brave",
                    "fallback_tools": ["tavily"],
                }
            }
        ),
    )

    assert result["ok"] is True
    assert result["source"] == "brave"
    assert brave.search_calls == 1
    assert tavily.search_calls == 0


def test_search_audit_records_selected_provider(tmp_path: Path) -> None:
    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    tavily = _LegacyHealthyTavilyProvider()
    search_plugin.register_provider(brave)
    search_plugin.register_provider(tavily)
    ctx = _audit_runtime_ctx(
        tmp_path,
        runtime_tools={
            "search": {
                "enabled_providers": ["brave", "tavily"],
                "default_provider": "brave",
                "provider_order": ["brave", "tavily"],
                "allow_fallback": True,
            }
        },
    )

    result = search_plugin._handle_web_search(
        {"query": "cats", "provider": "auto", "max_results": 5},
        ctx,
    )

    assert result["ok"] is True
    audit_path = ctx.run_root / "audit.jsonl"
    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        row.get("event") == "search.provider.selected"
        and row.get("selected_provider") == "brave"
        for row in records
    )


def test_search_uses_shared_run_provider_chain_helper(tmp_path: Path) -> None:
    from unittest.mock import patch

    brave = _ContextAwareBraveProvider(healthy_without_ctx=True)
    search_plugin.register_provider(brave)
    ctx = _audit_runtime_ctx(tmp_path)

    with patch(
        "openminion.tools.search.plugin.run_provider_chain",
        wraps=search_plugin.__class__.__module__  # sentinel — use real fn but spy
        and __import__(
            "openminion.modules.tool.family.runtime", fromlist=["run_provider_chain"]
        ).run_provider_chain,
    ) as mock_run:
        result = search_plugin._handle_web_search(
            {"query": "hello", "max_results": 3},
            ctx,
        )

    assert mock_run.called, "run_provider_chain must be called by _handle_web_search"
    assert result["ok"] is True
