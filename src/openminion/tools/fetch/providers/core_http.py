import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from ..interfaces import (
    FetchProviderProtocol,
    FetchRequest,
    ProviderCapabilities,
    ProviderResult,
)
from ..policy import (
    FetchPolicyError as _FetchPolicyError,
    enforce_url_policy as _shared_enforce_url_policy,
    resolve_allow_private_hosts as _resolve_allow_private_hosts,
)

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_CHARSET_RE = re.compile(r"charset=([A-Za-z0-9._-]+)", flags=re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", flags=re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_XML_CONTENT_TYPES = (
    "application/atom+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml",
)


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
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _bounded_int(value: Any, default: int, *, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


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


def _rate_limit_details(headers: dict[str, str]) -> dict[str, str]:
    retry_after = str(headers.get("retry-after", "") or "").strip()[:128]
    reset = str(headers.get("x-ratelimit-reset", "") or "").strip()[:128]
    details = {
        key: value
        for key, value in {"retry_after": retry_after, "reset": reset}.items()
        if value
    }
    now = datetime.now(timezone.utc)
    eligible: datetime | None = None
    if retry_after.isdigit():
        try:
            eligible = now + timedelta(seconds=int(retry_after))
        except (OverflowError, ValueError):
            eligible = None
    elif retry_after:
        try:
            eligible = parsedate_to_datetime(retry_after)
        except (OverflowError, TypeError, ValueError):
            eligible = None
    if eligible is None and reset.isdigit():
        try:
            eligible = datetime.fromtimestamp(int(reset), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            eligible = None
    if eligible is not None:
        if eligible.tzinfo is None:
            eligible = eligible.replace(tzinfo=timezone.utc)
        details["next_eligible_at"] = eligible.isoformat().replace("+00:00", "Z")
    return details


def _raise_for_status(response: _FetchStep) -> None:
    if response.status_code < 400:
        return
    details: dict[str, Any] = {
        "status_code": response.status_code,
        "url": response.final_url,
    }
    if response.status_code == 429:
        details.update(_rate_limit_details(response.headers))
    _fail(
        "RATE_LIMITED" if response.status_code == 429 else "UPSTREAM_ERROR",
        f"Upstream returned HTTP {response.status_code}",
        details,
    )


class CoreHttpFetchProvider(FetchProviderProtocol):
    name = "core-http"
    capabilities: ProviderCapabilities = {
        "render": ["none"],
        "extract": ["none", "text", "auto"],
        "formats": [
            "text/html",
            "text/plain",
            "application/json",
            *_XML_CONTENT_TYPES,
        ],
    }

    def fetch(self, request: FetchRequest, ctx: Any | None = None) -> ProviderResult:
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

            allow_private_hosts = _resolve_allow_private_hosts(dict(request), ctx)

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

            _raise_for_status(response_step)

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
                elif any(token in lowered_ct for token in _XML_CONTENT_TYPES):
                    extracted_text = decoded
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
