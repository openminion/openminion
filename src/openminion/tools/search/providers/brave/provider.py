"""Brave search provider."""

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from .constants import (
    BRAVE_SEARCH_API_ENDPOINT,
    BRAVE_SEARCH_DEFAULT_COUNT,
    BRAVE_SEARCH_DEFAULT_TIMEOUT_SECONDS,
    BRAVE_SEARCH_MAX_COUNT,
    BRAVE_SEARCH_MAX_OFFSET,
    BRAVE_SEARCH_PROVIDER_ID,
)


class BraveSearchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "UPSTREAM_ERROR",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class BraveSearchProviderConfig:
    endpoint: str = BRAVE_SEARCH_API_ENDPOINT
    timeout_s: float = BRAVE_SEARCH_DEFAULT_TIMEOUT_SECONDS
    api_key: str | None = None


def clamp_count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = BRAVE_SEARCH_DEFAULT_COUNT
    return max(1, min(BRAVE_SEARCH_MAX_COUNT, parsed))


def clamp_offset(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return max(0, min(BRAVE_SEARCH_MAX_OFFSET, parsed))


class BraveSearchProvider:
    provider_id = BRAVE_SEARCH_PROVIDER_ID

    def __init__(self, config: BraveSearchProviderConfig | None = None) -> None:
        self.config = config or BraveSearchProviderConfig()

    def _api_key(self, args: Mapping[str, Any], *, ctx: Any | None = None) -> str:
        if isinstance(args.get("api_key"), str) and str(args.get("api_key")).strip():
            return str(args.get("api_key")).strip()
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        env = getattr(ctx, "env", None) if ctx is not None else None
        if isinstance(env, EnvironmentConfig):
            return env.brave_api_key.strip()
        if isinstance(env, Mapping):
            return resolve_environment_config(runtime_env=env).brave_api_key.strip()
        return resolve_environment_config().brave_api_key.strip()

    def search(
        self, *, args: Mapping[str, Any], ctx: Any | None = None
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        api_key = self._api_key(args, ctx=ctx)
        if not api_key:
            raise BraveSearchError("Missing Brave API key", code="DEPENDENCY_MISSING")

        params: Dict[str, str] = {
            "q": str(args.get("q", "")).strip(),
            "count": str(clamp_count(args.get("count"))),
            "offset": str(clamp_offset(args.get("offset"))),
            "extra_snippets": "true"
            if bool(args.get("extra_snippets", False))
            else "false",
        }
        for key in ("country", "search_lang", "ui_lang", "safesearch"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                params[key] = value.strip()

        url = f"{self.config.endpoint}?{urllib_parse.urlencode(params)}"
        req = urllib_request.Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            method="GET",
        )

        try:
            with urllib_request.urlopen(
                req, timeout=float(self.config.timeout_s)
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise BraveSearchError(
                        "Unexpected Brave response shape", code="UPSTREAM_ERROR"
                    )
                headers = (
                    {k: v for k, v in resp.headers.items()} if resp.headers else {}
                )
                return payload, {
                    "X-RateLimit-Limit": str(headers.get("X-RateLimit-Limit", "")),
                    "X-RateLimit-Remaining": str(
                        headers.get("X-RateLimit-Remaining", "")
                    ),
                    "X-RateLimit-Reset": str(headers.get("X-RateLimit-Reset", "")),
                }
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            code = "UPSTREAM_ERROR"
            if status in (401, 403):
                code = "AUTH_FAILED"
            elif status == 429:
                code = "RATE_LIMITED"
            elif status == 422:
                code = "INVALID_REQUEST"
            raise BraveSearchError(
                f"Brave request failed with status {status}",
                code=code,
                details={"status": status, "body": body[:300]},
            ) from exc
        except (urllib_error.URLError, TimeoutError) as exc:
            raise BraveSearchError(
                "Brave request failed",
                code="UPSTREAM_ERROR",
                details={"reason": str(getattr(exc, "reason", exc))},
            ) from exc
