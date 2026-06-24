"""SerpAPI search provider."""

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.tools.config import resolve_tool_context_env
from openminion.tools.search.providers import SearchProviderError

from .config import (
    DEFAULT_SERPAPI_API_URL,
    DEFAULT_SERPAPI_TIMEOUT_SECONDS,
    SerpApiSearchProviderConfig,
    get_serpapi_api_key,
    get_serpapi_api_url,
    get_serpapi_timeout_seconds,
)
from .constants import SERPAPI_GOOGLE_ENGINE, SERPAPI_SEARCH_PROVIDER_ID


def _normalize_safe_search(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    if token in {"off", "false", "0", "no", "none"}:
        return "off"
    if token in {"moderate", "strict", "active", "on", "true", "1", "yes"}:
        return "active"
    return ""


def _error_code_for_status(status: int) -> str:
    if status in {400, 422}:
        return "INVALID_REQUEST"
    if status in {401, 403}:
        return "AUTH_FAILED"
    if status == 429:
        return "RATE_LIMITED"
    return "UPSTREAM_ERROR"


def _coerce_warning(raw: Any) -> str:
    warning = str(raw or "").strip()
    return warning


@dataclass(frozen=True)
class _SerpApiResponse:
    payload: Mapping[str, Any]
    http_status: int


class SerpApiSearchProvider:
    provider_id = SERPAPI_SEARCH_PROVIDER_ID
    display_name = "SerpApi Search"

    def __init__(self, config: SerpApiSearchProviderConfig | None = None) -> None:
        self.config = config or SerpApiSearchProviderConfig()

    def _api_key(self, args: Mapping[str, Any], *, ctx: Any | None = None) -> str:
        raw_arg = str(args.get("api_key", "") or "").strip()
        if raw_arg:
            return raw_arg
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        env = resolve_tool_context_env(ctx)
        return get_serpapi_api_key(env=env)

    def _api_url(self, *, ctx: Any | None = None) -> str:
        if self.config.endpoint and self.config.endpoint.strip():
            return self.config.endpoint.strip()
        env = resolve_tool_context_env(ctx)
        return get_serpapi_api_url(env=env) or DEFAULT_SERPAPI_API_URL

    def _timeout_seconds(self, *, ctx: Any | None = None) -> float:
        if self.config.timeout_s > 0:
            return float(self.config.timeout_s)
        env = resolve_tool_context_env(ctx)
        return get_serpapi_timeout_seconds(
            default=DEFAULT_SERPAPI_TIMEOUT_SECONDS,
            env=env,
        )

    def healthcheck(self, ctx: Any | None = None) -> bool:
        return bool(self._api_key({}, ctx=ctx))

    def _build_params(
        self,
        *,
        query: str,
        args: Mapping[str, Any],
        api_key: str,
    ) -> dict[str, str]:
        params: dict[str, str] = {
            "engine": SERPAPI_GOOGLE_ENGINE,
            "output": "json",
            "api_key": api_key,
            "q": query,
        }
        country = str(args.get("country", "") or "").strip()
        if country:
            params["gl"] = country
        search_lang = str(args.get("search_lang", "") or "").strip()
        ui_lang = str(args.get("ui_lang", "") or "").strip()
        hl = search_lang or ui_lang
        if hl:
            params["hl"] = hl
        safe = _normalize_safe_search(args.get("safesearch"))
        if safe:
            params["safe"] = safe
        return params

    def _request(
        self,
        *,
        params: Mapping[str, str],
        ctx: Any | None = None,
    ) -> _SerpApiResponse:
        url = f"{self._api_url(ctx=ctx)}?{urllib_parse.urlencode(params)}"
        request = urllib_request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib_request.urlopen(
                request, timeout=self._timeout_seconds(ctx=ctx)
            ) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw_body)
                if not isinstance(payload, dict):
                    raise SearchProviderError(
                        "SerpApi returned an unexpected payload shape",
                        code="UPSTREAM_ERROR",
                    )
                return _SerpApiResponse(
                    payload=payload,
                    http_status=int(getattr(response, "status", 200) or 200),
                )
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise SearchProviderError(
                f"SerpApi request failed with status {status}",
                code=_error_code_for_status(status),
                details={"status": status, "body": body[:500]},
            ) from exc
        except urllib_error.URLError as exc:
            raise SearchProviderError(
                "SerpApi request failed",
                code="UPSTREAM_ERROR",
                details={"reason": str(getattr(exc, "reason", exc))},
            ) from exc

    def _normalize_payload(
        self,
        *,
        query: str,
        payload: Mapping[str, Any],
        max_results: int,
    ) -> dict[str, Any]:
        warnings: list[str] = []

        search_metadata = payload.get("search_metadata")
        metadata_status = ""
        if isinstance(search_metadata, Mapping):
            metadata_status = str(search_metadata.get("status", "") or "").strip()

        top_level_error = _coerce_warning(payload.get("error"))
        if top_level_error:
            if metadata_status.lower() == "success" or not metadata_status:
                warnings.append(top_level_error)
            else:
                raise SearchProviderError(
                    top_level_error,
                    code="UPSTREAM_ERROR",
                    details={"status": metadata_status},
                )

        search_information = payload.get("search_information")
        if isinstance(search_information, Mapping):
            organic_state = _coerce_warning(
                search_information.get("organic_results_state")
            )
            if organic_state:
                warnings.append(f"organic_results_state={organic_state}")

        answer_box = payload.get("answer_box")
        answer = ""
        if isinstance(answer_box, Mapping):
            answer = _coerce_warning(answer_box.get("answer")) or _coerce_warning(
                answer_box.get("snippet")
            )

        raw_results = payload.get("organic_results")
        if not isinstance(raw_results, list):
            raw_results = []

        results: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_results[:max_results], start=1):
            if not isinstance(row, Mapping):
                continue
            title = str(row.get("title", "") or "").strip() or "Untitled"
            url = str(row.get("link", "") or "").strip()
            description = str(row.get("snippet", "") or "").strip()
            results.append(
                {
                    "rank": int(row.get("position", idx) or idx),
                    "title": title,
                    "url": url,
                    "description": description,
                }
            )

        serpapi_pagination = payload.get("serpapi_pagination")
        more_results_available = False
        if isinstance(serpapi_pagination, Mapping):
            more_results_available = bool(
                serpapi_pagination.get("next")
                or serpapi_pagination.get("next_link")
                or serpapi_pagination.get("next_page_token")
            )

        normalized: dict[str, Any] = {
            "provider": self.provider_id,
            "query": {
                "original": query,
                "more_results_available": more_results_available,
            },
            "results": results,
            "warnings": warnings,
        }
        if answer:
            normalized["answer"] = answer
        return normalized

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> Mapping[str, Any]:
        query_text = str(query or "").strip()
        if not query_text:
            raise SearchProviderError("query is required", code="INVALID_REQUEST")

        api_key = self._api_key(args, ctx=ctx)
        if not api_key:
            raise SearchProviderError(
                "Missing SerpApi API key",
                code="DEPENDENCY_MISSING",
            )

        response = self._request(
            params=self._build_params(query=query_text, args=args, api_key=api_key),
            ctx=ctx,
        )
        return self._normalize_payload(
            query=query_text,
            payload=response.payload,
            max_results=max(1, int(max_results or 1)),
        )


__all__ = [
    "SerpApiSearchProvider",
    "SerpApiSearchProviderConfig",
    "_error_code_for_status",
    "_normalize_safe_search",
]
