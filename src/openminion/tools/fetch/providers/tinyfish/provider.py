import json
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import (
    DEFAULT_TINYFISH_FETCH_API_URL,
    DEFAULT_TINYFISH_FETCH_TIMEOUT_SECONDS,
    TinyFishFetchProviderConfig,
    resolve_tinyfish_api_key,
    resolve_tinyfish_fetch_api_url,
    resolve_tinyfish_fetch_timeout_seconds,
)
from .constants import FETCH_TINYFISH_PROVIDER_ID
from .interfaces import FetchProviderProtocol, ProviderCapabilities
from .schemas import TinyFishProviderOptions


def _error_result(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
        },
        "backend": FETCH_TINYFISH_PROVIDER_ID,
    }


def _error_code_for_status(status: int) -> str:
    if status in {400, 422}:
        return "INVALID_REQUEST"
    if status == 401:
        return "AUTH_FAILED"
    if status == 429:
        return "RATE_LIMITED"
    return "UPSTREAM_ERROR"


def _error_code_for_row(error_code: str) -> str:
    normalized = str(error_code or "").strip().lower()
    if normalized == "invalid_url":
        return "INVALID_REQUEST"
    return "UPSTREAM_ERROR"


class TinyFishFetchProvider(FetchProviderProtocol):
    name = FETCH_TINYFISH_PROVIDER_ID
    capabilities: ProviderCapabilities = {
        "render": ["dom"],
        "extract": ["text"],
        "formats": ["markdown", "html", "json"],
    }

    def __init__(self, config: TinyFishFetchProviderConfig | None = None) -> None:
        self.config = config or TinyFishFetchProviderConfig()

    def _api_key(self, *, ctx: Any | None = None) -> str:
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        runtime_env = getattr(ctx, "env", None) if ctx is not None else None
        return resolve_tinyfish_api_key(env=runtime_env)

    def _api_url(self, *, ctx: Any | None = None) -> str:
        if self.config.endpoint and self.config.endpoint.strip():
            return self.config.endpoint.strip()
        runtime_env = getattr(ctx, "env", None) if ctx is not None else None
        return (
            resolve_tinyfish_fetch_api_url(env=runtime_env)
            or DEFAULT_TINYFISH_FETCH_API_URL
        )

    def _timeout_seconds(
        self,
        request: Mapping[str, Any],
        *,
        ctx: Any | None = None,
    ) -> float:
        explicit_timeout_ms = int(request.get("timeout_ms", 0) or 0)
        if explicit_timeout_ms > 0:
            return max(explicit_timeout_ms / 1000.0, 0.1)
        if self.config.timeout_s > 0:
            return float(self.config.timeout_s)
        runtime_env = getattr(ctx, "env", None) if ctx is not None else None
        return (
            resolve_tinyfish_fetch_timeout_seconds(
                default=DEFAULT_TINYFISH_FETCH_TIMEOUT_SECONDS,
                env=runtime_env,
            )
            or DEFAULT_TINYFISH_FETCH_TIMEOUT_SECONDS
        )

    def fetch(self, request: dict[str, Any], ctx: Any | None = None) -> dict[str, Any]:
        method = str(request.get("method", "GET") or "GET").strip().upper()
        if method == "HEAD":
            return _error_result(
                "INVALID_ARGUMENT",
                "TinyFish fetch does not support HEAD requests",
                details={"method": method},
            )
        if method != "GET":
            return _error_result(
                "INVALID_ARGUMENT",
                "Unsupported fetch method",
                details={"method": method},
            )

        api_key = self._api_key(ctx=ctx)
        if not api_key:
            return _error_result(
                "DEPENDENCY_MISSING",
                "Missing TinyFish API key",
            )

        provider_options = request.get("provider_options", {})
        tinyfish_payload = (
            provider_options.get("tinyfish", {})
            if isinstance(provider_options, dict)
            else {}
        )
        try:
            options = TinyFishProviderOptions.model_validate(tinyfish_payload)
        except Exception as exc:
            return _error_result(
                "INVALID_ARGUMENT",
                f"invalid tinyfish provider options: {exc}",
            )

        warnings: list[str] = []
        for unsupported in (
            "headers",
            "accept",
            "follow_redirects",
            "max_redirects",
            "max_bytes",
        ):
            if unsupported in request and request.get(unsupported) not in (
                {},
                None,
                True,
                5,
                2_000_000,
                "text/html,text/plain,application/json",
            ):
                warnings.append(f"UNSUPPORTED_REQUEST_FIELD:{unsupported}")

        body = {
            "urls": [str(request.get("url", "") or "").strip()],
            "format": options.format,
            "links": bool(options.links),
            "image_links": bool(options.image_links),
        }

        req = urllib_request.Request(
            self._api_url(ctx=ctx),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(
                req, timeout=self._timeout_seconds(request, ctx=ctx)
            ) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
                if not isinstance(payload, dict):
                    return _error_result(
                        "UPSTREAM_ERROR",
                        "TinyFish returned an unexpected payload shape",
                    )
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            return _error_result(
                _error_code_for_status(status),
                f"TinyFish request failed with status {status}",
                details={"status": status, "body": body_text[:1000]},
            )
        except urllib_error.URLError as exc:
            return _error_result(
                "UPSTREAM_ERROR",
                "TinyFish request failed",
                details={"reason": str(getattr(exc, "reason", exc))},
            )

        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            row = errors[0] if isinstance(errors[0], Mapping) else {}
            row_error = str(
                row.get("error", "") or row.get("code", "") or "fetch_error"
            )
            return _error_result(
                _error_code_for_row(row_error),
                f"TinyFish fetch returned per-url error: {row_error}",
                details={"row": dict(row) if isinstance(row, Mapping) else {}},
            )

        results = payload.get("results")
        if (
            not isinstance(results, list)
            or not results
            or not isinstance(results[0], Mapping)
        ):
            return _error_result(
                "UPSTREAM_ERROR",
                "TinyFish fetch returned no results",
            )

        row = results[0]
        payload_text = row.get("text", "")
        if options.format == "json":
            raw_body = json.dumps(payload_text, ensure_ascii=False)
            extracted_text = raw_body
            content_type = "application/json"
        else:
            raw_body = str(payload_text or "")
            extracted_text = raw_body
            content_type = (
                "text/markdown" if options.format == "markdown" else "text/html"
            )
        content_bytes = len(raw_body.encode("utf-8", errors="replace"))

        meta: dict[str, Any] = {}
        for key in (
            "description",
            "author",
            "published_date",
            "latency_ms",
            "links",
            "image_links",
            "format",
        ):
            if key in row:
                meta[key] = row.get(key)

        return {
            "ok": True,
            "final_url": str(row.get("final_url", row.get("url", "")) or "").strip(),
            "status_code": 200,
            "content_type": content_type,
            "content_bytes": content_bytes,
            "raw_body": raw_body,
            "extracted_text": extracted_text,
            "title": str(row.get("title", "") or "").strip(),
            "language": str(row.get("language", "") or "").strip(),
            "warnings": warnings,
            "meta": meta,
            "backend": FETCH_TINYFISH_PROVIDER_ID,
        }


provider = TinyFishFetchProvider()

__all__ = ["TinyFishFetchProvider", "provider"]
