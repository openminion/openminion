import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..interfaces import FetchProviderProtocol, ProviderCapabilities, ProviderResult
from ..policy import (
    FetchPolicyError as _FetchPolicyError,
    enforce_url_policy as _shared_enforce_url_policy,
    extract_fetch_policy as _shared_extract_fetch_policy,
)

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_CHARSET_RE = re.compile(r"charset=([A-Za-z0-9._-]+)", flags=re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", flags=re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)


@dataclass
class _FetchStep:
    status_code: int
    final_url: str
    headers: dict[str, str]
    body: bytes


class _FetchProviderError(Exception):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # type: ignore[override]
        return None


def _bounded_int(value: Any, default: int, *, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _extract_fetch_policy(ctx: Any | None) -> dict[str, Any]:
    return _shared_extract_fetch_policy(ctx)


def _fail(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise _FetchProviderError(code=code, message=message, details=details)


def _enforce_url_policy(
    url: str, *, allow_private_hosts: bool
) -> urllib.parse.ParseResult:
    try:
        return _shared_enforce_url_policy(url, allow_private_hosts=allow_private_hosts)
    except _FetchPolicyError as exc:
        raise _FetchProviderError(
            code=exc.code, message=exc.message, details=dict(exc.details)
        ) from exc


def _decode_body(content_type: str, payload: bytes) -> str:
    charset_match = _CHARSET_RE.search(str(content_type or ""))
    charset = charset_match.group(1).strip() if charset_match else "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_html_title(text: str) -> str:
    match = _TITLE_RE.search(text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1) or "")).strip()


def _extract_html_text(text: str) -> str:
    without_script = _SCRIPT_STYLE_RE.sub(" ", text)
    without_tags = _TAG_RE.sub(" ", without_script)
    unescaped = html.unescape(without_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _open_once(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    timeout_ms: int,
    max_bytes: int,
    read_body: bool,
) -> _FetchStep:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url=url, method=method, headers=headers)
    response: Any
    try:
        response = opener.open(request, timeout=max(timeout_ms, 1) / 1000.0)
    except urllib.error.HTTPError as exc:
        response = exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", "")
        if isinstance(reason, TimeoutError):
            _fail("TIMEOUT", "Fetch timed out", {"url": url})
        if isinstance(reason, OSError) and "timed out" in str(reason).lower():
            _fail("TIMEOUT", "Fetch timed out", {"url": url})
        _fail(
            "UPSTREAM_ERROR",
            "Network request failed",
            {"url": url, "reason": str(reason or exc)},
        )

    status_code = int(
        getattr(response, "status", 0) or getattr(response, "code", 0) or 0
    )
    final_url = str(response.geturl() or url)
    header_items = {}
    try:
        header_items = {
            str(key).lower(): str(value) for key, value in response.headers.items()
        }
    except Exception:
        header_items = {}

    body = b""
    if read_body and status_code not in _REDIRECT_CODES:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            _fail(
                "MAX_BYTES_EXCEEDED",
                "Response exceeded max_bytes",
                {"max_bytes": max_bytes, "url": final_url},
            )
    try:
        response.close()
    except Exception:
        pass
    return _FetchStep(
        status_code=status_code, final_url=final_url, headers=header_items, body=body
    )


class CoreHttpFetchProvider(FetchProviderProtocol):
    name = "core-http"
    capabilities: ProviderCapabilities = {
        "render": ["none"],
        "extract": ["none", "text", "auto"],
        "formats": ["text/html", "text/plain", "application/json"],
    }

    def fetch(self, request: dict[str, Any], ctx: Any | None = None) -> ProviderResult:
        started = time.time()
        try:
            requested_url = str(request.get("url", "")).strip()
            method = str(request.get("method", "GET") or "GET").strip().upper()
            if method not in {"GET", "HEAD"}:
                _fail(
                    "INVALID_ARGUMENT", "Unsupported fetch method", {"method": method}
                )

            timeout_ms = _bounded_int(
                request.get("timeout_ms"), 8000, lower=100, upper=30000
            )
            max_bytes = _bounded_int(
                request.get("max_bytes"), 2_000_000, lower=1_024, upper=10_000_000
            )
            max_redirects = _bounded_int(
                request.get("max_redirects"), 5, lower=0, upper=10
            )
            follow_redirects = bool(request.get("follow_redirects", True))

            headers: dict[str, str] = {}
            if isinstance(request.get("headers"), dict):
                headers = {
                    str(key): str(value)
                    for key, value in request["headers"].items()
                    if str(key).strip()
                }
            accept = str(request.get("accept", "")).strip()
            if accept and "accept" not in {key.lower() for key in headers}:
                headers["Accept"] = accept
            if "User-Agent" not in headers and "user-agent" not in {
                key.lower() for key in headers
            }:
                headers["User-Agent"] = "OpenMinionFetch/1.0"

            policy_cfg = _extract_fetch_policy(ctx)
            allow_private_hosts = bool(policy_cfg.get("allow_private_hosts", False))
            if bool(request.get("allow_private_hosts", False)):
                allow_private_hosts = True

            _enforce_url_policy(requested_url, allow_private_hosts=allow_private_hosts)

            current_url = requested_url
            redirect_hops = 0
            response_step: _FetchStep | None = None
            warnings: list[str] = []

            while True:
                _enforce_url_policy(
                    current_url, allow_private_hosts=allow_private_hosts
                )
                response_step = _open_once(
                    url=current_url,
                    method=method,
                    headers=headers,
                    timeout_ms=timeout_ms,
                    max_bytes=max_bytes,
                    read_body=method != "HEAD",
                )
                if response_step.status_code in _REDIRECT_CODES and follow_redirects:
                    location = response_step.headers.get("location", "").strip()
                    if not location:
                        break
                    if redirect_hops >= max_redirects:
                        _fail(
                            "REDIRECT_LIMIT_EXCEEDED",
                            "Redirect limit exceeded",
                            {"max_redirects": max_redirects, "url": current_url},
                        )
                    next_url = urllib.parse.urljoin(current_url, location)
                    _enforce_url_policy(
                        next_url, allow_private_hosts=allow_private_hosts
                    )
                    current_url = next_url
                    redirect_hops += 1
                    continue
                break

            if response_step is None:
                _fail(
                    "UPSTREAM_ERROR",
                    "No response received from upstream",
                    {"url": requested_url},
                )

            if response_step.status_code >= 400:
                _fail(
                    "UPSTREAM_ERROR",
                    f"Upstream returned HTTP {response_step.status_code}",
                    {
                        "status_code": response_step.status_code,
                        "url": response_step.final_url,
                    },
                )

            content_type = str(response_step.headers.get("content-type", "")).strip()
            extracted_text = ""
            title = ""
            language = ""
            if method != "HEAD":
                decoded = _decode_body(content_type, response_step.body)
                lowered_ct = content_type.lower()
                if "application/json" in lowered_ct:
                    try:
                        parsed = json.loads(decoded)
                        extracted_text = json.dumps(
                            parsed, ensure_ascii=False, indent=2
                        )
                    except json.JSONDecodeError:
                        extracted_text = decoded
                elif "text/html" in lowered_ct:
                    title = _extract_html_title(decoded)
                    extracted_text = _extract_html_text(decoded)
                    html_lang = re.search(
                        r"<html\b[^>]*\blang=['\"]?([^'\"> ]+)",
                        decoded,
                        flags=re.IGNORECASE,
                    )
                    if html_lang:
                        language = str(html_lang.group(1) or "").strip()
                elif lowered_ct.startswith("text/") or not lowered_ct:
                    extracted_text = decoded
                else:
                    warnings.append("UNSUPPORTED_CONTENT_TYPE")

            duration_ms = int((time.time() - started) * 1000)
            return {
                "ok": True,
                "final_url": response_step.final_url,
                "status_code": response_step.status_code,
                "headers": dict(response_step.headers),
                "content_type": content_type,
                "content_bytes": len(response_step.body),
                "raw_body": response_step.body,
                "extracted_text": extracted_text,
                "title": title,
                "language": language,
                "warnings": warnings,
                "meta": {
                    "duration_ms": duration_ms,
                    "redirect_hops": redirect_hops,
                    "hash": f"sha256:{hashlib.sha256(response_step.body).hexdigest()}",
                },
            }
        except _FetchProviderError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": dict(exc.details),
                },
                "warnings": [],
            }


provider = CoreHttpFetchProvider()


__all__ = ["CoreHttpFetchProvider", "provider"]
