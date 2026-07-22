"""Firecrawl search provider."""

import json
from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.tools.search.providers import SearchProviderError

from .config import (
    DEFAULT_FIRECRAWL_API_URL,
    DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
    FirecrawlSearchProviderConfig,
    resolve_firecrawl_api_key,
    resolve_firecrawl_api_url,
    resolve_firecrawl_timeout_seconds,
)
from .constants import (
    FIRECRAWL_SEARCH_API_PATH,
    FIRECRAWL_SEARCH_DISPLAY_NAME,
    FIRECRAWL_SEARCH_PROVIDER_ID,
)


def _search_url(base_url: str) -> str:
    normalized = str(base_url or DEFAULT_FIRECRAWL_API_URL).strip().rstrip("/")
    if normalized.endswith(FIRECRAWL_SEARCH_API_PATH):
        return normalized
    return f"{normalized}{FIRECRAWL_SEARCH_API_PATH}"


def _error_code_for_status(status: int) -> str:
    if status in {400, 422}:
        return "INVALID_REQUEST"
    if status in {401, 403}:
        return "AUTH_FAILED"
    if status == 429:
        return "RATE_LIMITED"
    return "UPSTREAM_ERROR"


@dataclass(frozen=True)
class _FirecrawlResponse:
    payload: Mapping[str, Any]
    http_status: int


class FirecrawlSearchProvider:
    provider_id = FIRECRAWL_SEARCH_PROVIDER_ID
    display_name = FIRECRAWL_SEARCH_DISPLAY_NAME

    def __init__(self, config: FirecrawlSearchProviderConfig | None = None) -> None:
        self.config = config or FirecrawlSearchProviderConfig()

    def _api_key(self, args: Mapping[str, Any], *, ctx: Any | None = None) -> str:
        raw_arg = str(args.get("api_key", "") or "").strip()
        if raw_arg:
            return raw_arg
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        return resolve_firecrawl_api_key(ctx=ctx)

    def _api_url(self, *, ctx: Any | None = None) -> str:
        if self.config.endpoint and self.config.endpoint.strip():
            return _search_url(self.config.endpoint)
        return _search_url(
            resolve_firecrawl_api_url(ctx=ctx) or DEFAULT_FIRECRAWL_API_URL
        )

    def _timeout_seconds(self, *, ctx: Any | None = None) -> float:
        if self.config.timeout_s > 0:
            return float(self.config.timeout_s)
        return (
            resolve_firecrawl_timeout_seconds(ctx=ctx)
            or DEFAULT_FIRECRAWL_TIMEOUT_SECONDS
        )

    def healthcheck(self, ctx: Any | None = None) -> bool:
        return bool(self._api_key({}, ctx=ctx))

    def _build_body(
        self,
        *,
        query: str,
        max_results: int,
        args: Mapping[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": query,
            "limit": int(max_results),
            "sources": ["web"],
        }
        country = str(args.get("country", "") or "").strip()
        if country:
            body["country"] = country
        return body

    def _request(
        self,
        *,
        body: Mapping[str, Any],
        api_key: str,
        ctx: Any | None = None,
    ) -> _FirecrawlResponse:
        request = urllib_request.Request(
            self._api_url(ctx=ctx),
            data=json.dumps(dict(body)).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                request, timeout=self._timeout_seconds(ctx=ctx)
            ) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw_body)
                if not isinstance(payload, dict):
                    raise SearchProviderError(
                        "Firecrawl returned an unexpected payload shape",
                        code="UPSTREAM_ERROR",
                    )
                return _FirecrawlResponse(
                    payload=payload,
                    http_status=int(getattr(response, "status", 200) or 200),
                )
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            raise SearchProviderError(
                f"Firecrawl request failed with status {status}",
                code=_error_code_for_status(status),
                details={"status": status, "body": body_text[:500]},
            ) from exc
        except urllib_error.URLError as exc:
            raise SearchProviderError(
                "Firecrawl request failed",
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
        data = payload.get("data")
        if isinstance(data, Mapping):
            web_rows = data.get("web")
        else:
            web_rows = None
        if not isinstance(web_rows, list):
            web_rows = []

        results: list[dict[str, Any]] = []
        for idx, row in enumerate(web_rows[:max_results], start=1):
            if not isinstance(row, Mapping):
                continue
            results.append(
                {
                    "rank": int(row.get("position", idx) or idx),
                    "title": str(row.get("title", "") or "").strip() or "Untitled",
                    "url": str(row.get("url", "") or "").strip(),
                    "description": str(
                        row.get(
                            "description", row.get("snippet", row.get("markdown", ""))
                        )
                        or ""
                    ).strip(),
                }
            )

        warnings: list[str] = []
        warning = str(payload.get("warning", "") or "").strip()
        if warning:
            warnings.append(warning)

        return {
            "provider": self.provider_id,
            "query": {"original": query, "more_results_available": False},
            "results": results,
            "warnings": warnings,
        }

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
                "Missing Firecrawl API key",
                code="DEPENDENCY_MISSING",
            )

        response = self._request(
            body=self._build_body(
                query=query_text,
                max_results=max_results,
                args=args,
            ),
            api_key=api_key,
            ctx=ctx,
        )
        return self._normalize_payload(
            query=query_text,
            payload=response.payload,
            max_results=max_results,
        )


__all__ = [
    "FirecrawlSearchProvider",
    "FirecrawlSearchProviderConfig",
]
