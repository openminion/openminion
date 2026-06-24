"""Brave search provider plugin."""

from collections.abc import Mapping
import inspect
from typing import Any

from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.search import register_provider
from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.brave.provider import (
    BraveSearchError,
    BraveSearchProvider,
    clamp_count,
    clamp_offset,
)


class BraveSearchFacadeProvider:
    provider_id = "brave"
    display_name = "Brave Search"

    def __init__(self, provider: BraveSearchProvider | None = None) -> None:
        self._provider = provider or BraveSearchProvider()
        try:
            params = inspect.signature(self._provider.search).parameters
            self._provider_accepts_ctx = "ctx" in params
        except Exception:
            self._provider_accepts_ctx = True

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> dict[str, Any]:
        request_args: dict[str, Any] = {
            "q": query,
            "count": clamp_count(args.get("count", max_results)),
            "offset": clamp_offset(args.get("offset", 0)),
            "extra_snippets": bool(args.get("extra_snippets", False)),
        }
        for key in ("country", "search_lang", "ui_lang", "safesearch", "api_key"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                request_args[key] = value.strip()

        try:
            if self._provider_accepts_ctx:
                payload, headers = self._provider.search(args=request_args, ctx=ctx)
            else:
                payload, headers = self._provider.search(args=request_args)
        except BraveSearchError as exc:
            raise SearchProviderError(
                f"Brave search failed ({exc.code}): {exc}",
                code=str(exc.code or "UPSTREAM_ERROR"),
                details=dict(exc.details or {}),
            ) from exc

        web_payload = (
            payload.get("web") if isinstance(payload.get("web"), Mapping) else {}
        )
        rows = (
            web_payload.get("results")
            if isinstance(web_payload.get("results"), list)
            else payload.get("results")
        )
        if not isinstance(rows, list):
            rows = []

        normalized_results: list[dict[str, Any]] = []
        for idx, row in enumerate(rows[: int(request_args["count"])], start=1):
            if not isinstance(row, Mapping):
                continue
            item: dict[str, Any] = {
                "rank": idx,
                "title": str(row.get("title") or "Untitled"),
                "url": str(row.get("url") or ""),
                "description": str(row.get("description") or row.get("snippet") or ""),
            }
            snippets = row.get("extra_snippets")
            if isinstance(snippets, list):
                item["extra_snippets"] = [
                    str(value) for value in snippets if str(value)
                ]
            normalized_results.append(item)

        query_payload = (
            payload.get("query") if isinstance(payload.get("query"), Mapping) else {}
        )
        normalized: dict[str, Any] = {
            "provider": "brave",
            "query": {
                "original": str(query_payload.get("original") or query),
                "more_results_available": bool(
                    query_payload.get("more_results_available", False)
                ),
            },
            "results": normalized_results,
        }

        rate_limit = {
            "limit": str(headers.get("X-RateLimit-Limit", "") or "").strip(),
            "remaining": str(headers.get("X-RateLimit-Remaining", "") or "").strip(),
            "reset": str(headers.get("X-RateLimit-Reset", "") or "").strip(),
        }
        rate_limit = {k: v for k, v in rate_limit.items() if v}
        if rate_limit:
            normalized["rate_limit"] = rate_limit

        return normalized

    def healthcheck(self, ctx: Any | None = None) -> bool:
        try:
            return bool(self._provider._api_key({}, ctx=ctx))
        except TypeError:
            return bool(self._provider._api_key({}))


def register(registry: ToolRegistry | None = None) -> None:
    register_provider(BraveSearchFacadeProvider())
    if registry is not None:
        del registry


def register_search_provider(registry: object) -> None:
    register(None)
    del registry


__all__ = ["BraveSearchFacadeProvider", "register", "register_search_provider"]
