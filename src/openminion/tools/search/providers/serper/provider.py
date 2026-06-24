"""Serper search provider."""

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.tools.config import resolve_tool_context_env
from openminion.tools.search.providers import SearchProviderError

from .config import (
    DEFAULT_SERPER_API_URL,
    DEFAULT_SERPER_TIMEOUT_SECONDS,
    SerperSearchProviderConfig,
    get_serper_api_key,
    get_serper_api_url,
    get_serper_timeout_seconds,
)
from .constants import SERPER_SEARCH_DISPLAY_NAME, SERPER_SEARCH_PROVIDER_ID


def _error_code_for_status(status: int) -> str:
    if status in {400, 422}:
        return "INVALID_REQUEST"
    if status in {401, 403}:
        return "AUTH_FAILED"
    if status == 429:
        return "RATE_LIMITED"
    return "UPSTREAM_ERROR"


@dataclass(frozen=True)
class _SerperResponse:
    payload: Mapping[str, Any]
    http_status: int


class SerperSearchProvider:
    provider_id = SERPER_SEARCH_PROVIDER_ID
    display_name = SERPER_SEARCH_DISPLAY_NAME

    def __init__(self, config: SerperSearchProviderConfig | None = None) -> None:
        self.config = config or SerperSearchProviderConfig()

    def _api_key(self, args: Mapping[str, Any], *, ctx: Any | None = None) -> str:
        raw_arg = str(args.get("api_key", "") or "").strip()
        if raw_arg:
            return raw_arg
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        env = resolve_tool_context_env(ctx)
        return get_serper_api_key(env=env)

    def _api_url(self, *, ctx: Any | None = None) -> str:
        if self.config.endpoint and self.config.endpoint.strip():
            return self.config.endpoint.strip()
        env = resolve_tool_context_env(ctx)
        return get_serper_api_url(env=env) or DEFAULT_SERPER_API_URL

    def _timeout_seconds(self, *, ctx: Any | None = None) -> float:
        if self.config.timeout_s > 0:
            return float(self.config.timeout_s)
        env = resolve_tool_context_env(ctx)
        return get_serper_timeout_seconds(
            default=DEFAULT_SERPER_TIMEOUT_SECONDS,
            env=env,
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
        body: dict[str, Any] = {"q": query, "num": int(max_results)}
        country = str(args.get("country", "") or "").strip().lower()
        if country:
            body["gl"] = country
        ui_lang = str(args.get("ui_lang", "") or "").strip()
        search_lang = str(args.get("search_lang", "") or "").strip()
        hl = ui_lang or search_lang
        if hl:
            body["hl"] = hl
        return body

    def _request(
        self,
        *,
        body: Mapping[str, Any],
        api_key: str,
        ctx: Any | None = None,
    ) -> _SerperResponse:
        request = urllib_request.Request(
            self._api_url(ctx=ctx),
            data=json.dumps(dict(body)).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-API-KEY": api_key,
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
                        "Serper returned an unexpected payload shape",
                        code="UPSTREAM_ERROR",
                    )
                return _SerperResponse(
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
                f"Serper request failed with status {status}",
                code=_error_code_for_status(status),
                details={"status": status, "body": body_text[:500]},
            ) from exc
        except urllib_error.URLError as exc:
            raise SearchProviderError(
                "Serper request failed",
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
        raw_results = payload.get("organic")
        if not isinstance(raw_results, list):
            raw_results = []

        results: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_results[:max_results], start=1):
            if not isinstance(row, Mapping):
                continue
            results.append(
                {
                    "rank": int(row.get("position", idx) or idx),
                    "title": str(row.get("title", "") or "").strip() or "Untitled",
                    "url": str(row.get("link", "") or "").strip(),
                    "description": str(row.get("snippet", "") or "").strip(),
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
                "Missing Serper API key",
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
