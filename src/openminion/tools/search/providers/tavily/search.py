from collections.abc import Mapping
import json
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.tools.config import resolve_tool_context_env
from openminion.tools.env import get_tavily_api_key, get_tavily_api_url

from .constants import (
    DEFAULT_TAVILY_API_URL,
    DEFAULT_TAVILY_MAX_SEARCH_RETRIES,
    DEFAULT_TAVILY_SEARCH_RETRY_BACKOFF,
)
from .normalization import (
    _coerce_bool,
    _coerce_int,
    _normalize_results,
    _normalize_search_depth,
)

_MAX_SEARCH_RETRIES = DEFAULT_TAVILY_MAX_SEARCH_RETRIES
_SEARCH_RETRY_BACKOFF = DEFAULT_TAVILY_SEARCH_RETRY_BACKOFF


def _error_result(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "content": "",
        "error": message,
        "verified": False,
    }


class TavilySearchTool:
    def __init__(
        self,
        *,
        api_key: str = "",
        api_url: str = "",
        timeout_seconds: float = 12.0,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._api_url = str(api_url or "").strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    def execute(self, arguments: Mapping[str, Any], context: Any) -> dict[str, Any]:
        request = self._build_search_request(arguments, context)
        if isinstance(error := request.get("error"), dict):
            return error

        for attempt in range(1, _MAX_SEARCH_RETRIES + 1):
            result = self._execute_search_attempt(request=request, attempt=attempt)
            if result is not None:
                return result
        return _error_result("Tavily search failed: unexpected error")

    def _build_search_request(
        self, arguments: Mapping[str, Any], context: Any
    ) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": _error_result("missing required argument 'query'")}
        api_key = self._resolve_api_key(context)
        if not api_key:
            return {
                "error": _error_result("missing Tavily API key (set TAVILY_API_KEY)")
            }
        max_results = _coerce_int(
            arguments.get("max_results"), default_value=5, minimum=1, maximum=10
        )
        return {
            "api_url": self._resolve_api_url(context),
            "payload": {
                "api_key": api_key,
                "query": query,
                "search_depth": _normalize_search_depth(arguments.get("search_depth")),
                "include_answer": _coerce_bool(
                    arguments.get("include_answer"), default_value=True
                ),
                "include_raw_content": False,
                "max_results": max_results,
            },
            "max_results": max_results,
        }

    def _execute_search_attempt(
        self, *, request: Mapping[str, Any], attempt: int
    ) -> dict[str, Any] | None:
        try:
            response_payload = self._search_tavily(
                api_url=str(request["api_url"]), payload=request["payload"]
            )
        except Exception as exc:
            return self._retry_or_error(
                attempt, f"Tavily search failed: {exc}", final_error=None
            )
        normalized_results = _normalize_results(response_payload.get("results"))
        answer = str(response_payload.get("answer", "")).strip()
        if not normalized_results and not answer:
            return self._retry_or_error(
                attempt,
                "empty search results",
                final_error="Tavily search returned empty results after retries",
            )
        return self._success_result(
            response_payload=response_payload,
            normalized_results=normalized_results,
            answer=answer,
            query=str(request["payload"]["query"]),
            search_depth=str(request["payload"]["search_depth"]),
            max_results=int(request["max_results"]),
        )

    def _retry_or_error(
        self, attempt: int, error: str, *, final_error: str | None
    ) -> dict[str, Any] | None:
        if attempt < _MAX_SEARCH_RETRIES:
            import time

            time.sleep(
                _SEARCH_RETRY_BACKOFF[min(attempt - 1, len(_SEARCH_RETRY_BACKOFF) - 1)]
            )
            return None
        return _error_result(final_error or error)

    def _success_result(
        self,
        *,
        response_payload: Mapping[str, Any],
        normalized_results: list[dict[str, Any]],
        answer: str,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "query": query,
            "answer": answer,
            "results": normalized_results,
            "result_count": len(normalized_results),
            "search_depth": search_depth,
            "max_results": max_results,
            "source": "tavily",
        }
        provider_query = response_payload.get("query")
        if isinstance(provider_query, str) and provider_query.strip():
            data["provider_query"] = provider_query.strip()
        if isinstance(
            response_time := response_payload.get("response_time"), (int, float)
        ):
            data["response_time"] = float(response_time)
        return {
            "ok": True,
            "content": _format_web_search_content(data),
            "verified": _verify_web_search_payload(data),
            "data": data,
            "source": "tavily",
        }

    def _search_tavily(
        self, *, api_url: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        request = urllib_request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                raw_body = response.read().decode("utf-8")
        except Exception as exc:
            reason = exc.reason if isinstance(exc, urllib_error.URLError) else exc
            raise RuntimeError(str(reason)) from exc

        try:
            decoded = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid json response from Tavily") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("unexpected Tavily response payload")
        return decoded

    def _resolve_api_key(self, context: Any = None) -> str:
        if self._api_key:
            return self._api_key
        return get_tavily_api_key(env=resolve_tool_context_env(context))

    def _resolve_api_url(self, context: Any = None) -> str:
        if self._api_url:
            return self._api_url
        return (
            get_tavily_api_url(env=resolve_tool_context_env(context))
            or DEFAULT_TAVILY_API_URL
        )


def _format_web_search_content(payload: Mapping[str, Any]) -> str:
    query = str(payload.get("query", "")).strip() or "unknown query"
    answer = str(payload.get("answer", "")).strip()
    results = payload.get("results")
    if not isinstance(results, list):
        results = []
    lines = [f'Web search for "{query}" returned {len(results)} result(s).']
    if answer:
        lines.append(f"Answer: {answer}")
    for idx, item in enumerate(results[:3], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip() or "Untitled"
        url = str(item.get("url", "")).strip() or "unknown-url"
        lines.append(f"{idx}. {title} - {url}")
    lines.append("source=tavily")
    return "\n".join(lines)


def _verify_web_search_payload(payload: Mapping[str, Any]) -> bool:
    query = str(payload.get("query", "")).strip()
    if not query:
        return False
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return False
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip().lower()
        if url.startswith("http://") or url.startswith("https://"):
            return True
    return False


__all__ = ["TavilySearchTool"]
