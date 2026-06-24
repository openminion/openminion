import json
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import (
    DEFAULT_FIRECRAWL_API_URL,
    DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
    FirecrawlFetchProviderConfig,
    resolve_firecrawl_api_key,
    resolve_firecrawl_api_url,
    resolve_firecrawl_timeout_seconds,
)
from .constants import (
    DEFAULT_FIRECRAWL_FORMATS,
    FETCH_FIRECRAWL_PROVIDER_ID,
    FIRECRAWL_SCRAPE_API_PATH,
)
from .interfaces import FetchProviderProtocol, ProviderCapabilities
from .schemas import FirecrawlProviderOptions


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
        "backend": FETCH_FIRECRAWL_PROVIDER_ID,
    }


def _error_code_for_status(status: int) -> str:
    if status in {400, 422}:
        return "INVALID_REQUEST"
    if status == 401:
        return "AUTH_FAILED"
    if status == 402:
        return "UPSTREAM_ERROR"  # spec §4.10 -- payment-required surfaces as upstream
    if status == 429:
        return "RATE_LIMITED"
    return "UPSTREAM_ERROR"


def _scrape_url(base_url: str) -> str:
    normalized = str(base_url or DEFAULT_FIRECRAWL_API_URL).strip().rstrip("/")
    if normalized.endswith(FIRECRAWL_SCRAPE_API_PATH):
        return normalized
    return f"{normalized}{FIRECRAWL_SCRAPE_API_PATH}"


def _camel_case_options(options: FirecrawlProviderOptions) -> dict[str, Any]:
    """Map our snake_case provider options into Firecrawl's camelCase body keys."""

    payload: dict[str, Any] = {}
    if options.only_main_content is not None:
        payload["onlyMainContent"] = bool(options.only_main_content)
    if options.include_tags is not None:
        payload["includeTags"] = [str(item) for item in options.include_tags]
    if options.exclude_tags is not None:
        payload["excludeTags"] = [str(item) for item in options.exclude_tags]
    if options.wait_for_ms is not None:
        payload["waitFor"] = int(options.wait_for_ms)
    if options.mobile is not None:
        payload["mobile"] = bool(options.mobile)
    if options.max_age_ms is not None:
        payload["maxAge"] = int(options.max_age_ms)
    if options.block_ads is not None:
        payload["blockAds"] = bool(options.block_ads)
    return payload


def _pick_raw_body(data: Mapping[str, Any]) -> tuple[str, str]:
    """Return (raw_body, content_type) using spec §4.8 mapping rules."""

    raw_html = data.get("rawHtml")
    if isinstance(raw_html, str) and raw_html:
        return raw_html, "text/html"
    html_body = data.get("html")
    if isinstance(html_body, str) and html_body:
        return html_body, "text/html"
    markdown = data.get("markdown")
    if isinstance(markdown, str) and markdown:
        return markdown, "text/markdown"
    return "", ""


class FirecrawlFetchProvider(FetchProviderProtocol):
    name = FETCH_FIRECRAWL_PROVIDER_ID
    capabilities: ProviderCapabilities = {
        "render": ["dom"],
        "extract": ["text", "markdown"],
        "formats": list(DEFAULT_FIRECRAWL_FORMATS),
    }

    def __init__(self, config: FirecrawlFetchProviderConfig | None = None) -> None:
        self.config = config or FirecrawlFetchProviderConfig()

    def _api_key(self, *, ctx: Any | None = None) -> str:
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        runtime_env = getattr(ctx, "env", None) if ctx is not None else None
        return resolve_firecrawl_api_key(env=runtime_env)

    def _api_url(self, *, ctx: Any | None = None) -> str:
        if self.config.endpoint and self.config.endpoint.strip():
            return _scrape_url(self.config.endpoint)
        runtime_env = getattr(ctx, "env", None) if ctx is not None else None
        return _scrape_url(
            resolve_firecrawl_api_url(env=runtime_env) or DEFAULT_FIRECRAWL_API_URL
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
            resolve_firecrawl_timeout_seconds(
                default=DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
                env=runtime_env,
            )
            or DEFAULT_FIRECRAWL_TIMEOUT_SECONDS
        )

    def fetch(self, request: dict[str, Any], ctx: Any | None = None) -> dict[str, Any]:
        method = str(request.get("method", "GET") or "GET").strip().upper()
        if method == "HEAD":
            # Spec §4.7: HEAD is not handled natively. The facade enforces the
            return _error_result(
                "INVALID_ARGUMENT",
                "Firecrawl fetch does not support HEAD requests",
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
                "Missing Firecrawl API key",
            )

        url = str(request.get("url", "") or "").strip()
        if not url:
            return _error_result(
                "INVALID_REQUEST",
                "Firecrawl fetch requires a non-empty url",
            )

        provider_options = request.get("provider_options", {})
        firecrawl_payload = (
            provider_options.get("firecrawl", {})
            if isinstance(provider_options, dict)
            else {}
        )
        try:
            options = FirecrawlProviderOptions.model_validate(firecrawl_payload)
        except Exception as exc:
            return _error_result(
                "INVALID_ARGUMENT",
                f"invalid firecrawl provider options: {exc}",
            )

        formats = list(options.formats or DEFAULT_FIRECRAWL_FORMATS)
        body: dict[str, Any] = {
            "url": url,
            "formats": formats,
        }

        headers_arg = request.get("headers")
        if isinstance(headers_arg, dict) and headers_arg:
            body["headers"] = {str(k): str(v) for k, v in headers_arg.items()}

        # timeout_ms -> Firecrawl `timeout` (milliseconds) when provided.
        explicit_timeout_ms = int(request.get("timeout_ms", 0) or 0)
        if explicit_timeout_ms > 0:
            body["timeout"] = explicit_timeout_ms

        body.update(_camel_case_options(options))

        req = urllib_request.Request(
            self._api_url(ctx=ctx),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(
                req, timeout=self._timeout_seconds(request, ctx=ctx)
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return _error_result(
                        "UPSTREAM_ERROR",
                        "Firecrawl returned a malformed JSON payload",
                        details={"reason": str(exc)},
                    )
                if not isinstance(payload, dict):
                    return _error_result(
                        "UPSTREAM_ERROR",
                        "Firecrawl returned an unexpected payload shape",
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
                f"Firecrawl request failed with status {status}",
                details={"status": status, "body": body_text[:1000]},
            )
        except urllib_error.URLError as exc:
            return _error_result(
                "UPSTREAM_ERROR",
                "Firecrawl request failed",
                details={"reason": str(getattr(exc, "reason", exc))},
            )

        if not bool(payload.get("success", True)):
            err = payload.get("error", "Firecrawl reported failure")
            return _error_result(
                "UPSTREAM_ERROR",
                str(err)
                if not isinstance(err, Mapping)
                else "Firecrawl reported failure",
                details={"raw": payload},
            )

        data = payload.get("data")
        if not isinstance(data, Mapping):
            return _error_result(
                "UPSTREAM_ERROR",
                "Firecrawl response is missing data block",
            )

        metadata = (
            data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
        )
        status_code = int(metadata.get("statusCode", 200) or 200)
        content_type = str(metadata.get("contentType", "") or "")
        final_url = (
            str(metadata.get("sourceURL", metadata.get("url", "")) or url).strip()
            or url
        )
        title = str(metadata.get("title", "") or "").strip()
        language = str(metadata.get("language", "") or "").strip()

        raw_body, derived_content_type = _pick_raw_body(data)
        if not content_type:
            content_type = derived_content_type

        markdown = data.get("markdown")
        extracted_text = (
            markdown.strip()
            if isinstance(markdown, str) and markdown.strip()
            else raw_body
        )

        content_bytes = len(raw_body.encode("utf-8", errors="replace"))

        warnings: list[str] = []
        warning = str(data.get("warning", "") or "").strip()
        if warning:
            warnings.append(warning)
        meta_error = str(metadata.get("error", "") or "").strip()
        if meta_error:
            warnings.append(meta_error)

        meta: dict[str, Any] = {}
        if "links" in data:
            meta["links"] = data.get("links")
        if formats:
            meta["formats"] = list(formats)

        return {
            "ok": True,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "content_bytes": content_bytes,
            "raw_body": raw_body,
            "extracted_text": extracted_text,
            "title": title,
            "language": language,
            "warnings": warnings,
            "meta": meta,
            "backend": FETCH_FIRECRAWL_PROVIDER_ID,
        }


provider = FirecrawlFetchProvider()


__all__ = ["FirecrawlFetchProvider", "provider"]
