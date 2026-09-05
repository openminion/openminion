from collections.abc import Mapping
from typing import Any

from openminion.tools.config import resolve_tool_context_env
from openminion.tools.env import (
    get_tavily_api_key,
    get_tavily_api_url,
    get_tavily_timeout_seconds,
)
from openminion.tools.search.constants import SEARCH_TAVILY_PROVIDER_ID
from openminion.tools.search.providers import SearchProviderError

from .config import DEFAULT_TAVILY_TIMEOUT_SECONDS
from .constants import DEFAULT_TAVILY_API_URL, TAVILY_SEARCH_DEPTH_VALUES
from .interfaces import TAVILY_PLUGIN_INTERFACE_VERSION
from .search import (
    TavilySearchTool,
    _format_web_search_content,
    _verify_web_search_payload,
)


class TavilySearchProvider:
    provider_id = SEARCH_TAVILY_PROVIDER_ID
    display_name = "Tavily"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> dict[str, Any]:
        env = resolve_tool_context_env(ctx)
        api_key = get_tavily_api_key(env=env)
        if not api_key:
            raise SearchProviderError(
                "Missing Tavily API key",
                code="DEPENDENCY_MISSING",
            )
        api_url = get_tavily_api_url(env=env) or DEFAULT_TAVILY_API_URL
        timeout = get_tavily_timeout_seconds(
            default=DEFAULT_TAVILY_TIMEOUT_SECONDS,
            env=env,
        )

        tool = TavilySearchTool(
            api_key=api_key, api_url=api_url, timeout_seconds=timeout
        )
        payload_args: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
        }
        search_depth = str(args.get("search_depth", "")).strip().lower()
        if search_depth in TAVILY_SEARCH_DEPTH_VALUES:
            payload_args["search_depth"] = search_depth
        include_answer = args.get("include_answer")
        if isinstance(include_answer, bool):
            payload_args["include_answer"] = include_answer

        result = tool.execute(payload_args, ctx)
        if not result.get("ok"):
            raise SearchProviderError(
                str(result.get("error") or "Tavily search failed").strip(),
                code="UPSTREAM_ERROR",
            )

        data = result["data"]
        normalized_rows = [
            {
                "rank": idx,
                "title": str(row.get("title") or "Untitled"),
                "url": str(row.get("url") or ""),
                "description": str(
                    row.get("description", row.get("snippet", row.get("content", "")))
                    or ""
                ),
            }
            for idx, row in enumerate(data["results"][:max_results], start=1)
        ]

        normalized: dict[str, Any] = {
            "provider": SEARCH_TAVILY_PROVIDER_ID,
            "query": {
                "original": str(data.get("query", query) or query),
                "more_results_available": False,
            },
            "results": normalized_rows,
        }
        answer = str(data.get("answer", "") or "").strip()
        if answer:
            normalized["answer"] = answer
        return normalized

    def healthcheck(self, ctx: Any | None = None) -> bool:
        return bool(get_tavily_api_key(env=resolve_tool_context_env(ctx)))


_PROVIDER = TavilySearchProvider()


def register(registry: Any | None = None) -> None:
    del registry
    from openminion.tools.search import register_provider

    register_provider(_PROVIDER)


register_search_provider = register


class TavilySearchPlugin:
    tool_id = "search.provider.tavily"
    contract_version = TAVILY_PLUGIN_INTERFACE_VERSION
    capabilities = ("network", "web.search", "tavily")
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
            "search_depth": {"type": "string", "enum": ["basic", "advanced"]},
            "include_answer": {"type": "boolean"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "content": {"type": "string"},
            "verified": {"type": "boolean"},
            "error": {"type": ["string", "null"]},
            "data": {"type": "object"},
        },
        "required": ["ok", "content", "verified"],
    }

    def register(self, registry: Any) -> None:
        register(registry)

    def healthcheck(self) -> dict[str, Any]:
        configured = bool(get_tavily_api_key())
        return {
            "ok": True,
            "tool_id": self.tool_id,
            "configured": configured,
            "api_url": get_tavily_api_url() or DEFAULT_TAVILY_API_URL,
        }


__all__ = [
    "TavilySearchPlugin",
    "TavilySearchProvider",
    "TavilySearchTool",
    "_format_web_search_content",
    "_verify_web_search_payload",
    "register",
    "register_search_provider",
]
