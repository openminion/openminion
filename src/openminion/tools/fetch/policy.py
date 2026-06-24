import ipaddress
import socket
import urllib.parse
from typing import Any

from openminion.modules.tool.runtime.network import (
    is_forbidden_ip as _is_forbidden_ip_impl,
)

_SCHEME_ALLOWLIST = {"http", "https"}


class FetchPolicyError(Exception):
    """Raised when shared URL/SSRF policy rejects a fetch request."""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


def _fail(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise FetchPolicyError(code=code, message=message, details=details)


def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:  # type: ignore[attr-defined]
    return _is_forbidden_ip_impl(ip)


def extract_fetch_policy(ctx: Any | None) -> dict[str, Any]:
    """Return the `tools.fetch` policy block from a runtime context, if any."""

    if ctx is None:
        return {}
    policy = getattr(ctx, "policy", None)
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, dict):
        return {}
    tools_cfg = raw.get("tools")
    if not isinstance(tools_cfg, dict):
        return {}
    fetch_cfg = tools_cfg.get("fetch")
    if not isinstance(fetch_cfg, dict):
        return {}
    return dict(fetch_cfg)


def enforce_url_policy(
    url: str, *, allow_private_hosts: bool
) -> urllib.parse.ParseResult:
    """Validate a URL against scheme allowlist and SSRF rules."""

    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in _SCHEME_ALLOWLIST:
        _fail("SCHEME_NOT_ALLOWED", "Only http/https URLs are allowed", {"url": url})
    if not parsed.netloc:
        _fail("INVALID_URL", "URL must be absolute and include host", {"url": url})
    host = str(parsed.hostname or "").strip()
    if not host:
        _fail("INVALID_URL", "URL host is missing", {"url": url})
    if allow_private_hosts:
        return parsed

    lowered = host.lower()
    if lowered in {"localhost", "ip6-localhost", "0.0.0.0"}:
        _fail(
            "SSRF_BLOCKED",
            "Target host is not allowed by SSRF policy",
            {"host": host},
        )

    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None and _is_forbidden_ip(direct_ip):
        _fail(
            "SSRF_BLOCKED",
            "Target host is not allowed by SSRF policy",
            {"host": host},
        )

    try:
        resolved = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        _fail(
            "SSRF_BLOCKED",
            "Target host could not be resolved by SSRF policy",
            {
                "host": host,
                "reason_code": "ssrf_resolution_failed",
                "error_type": type(exc).__name__,
            },
        )
    except Exception as exc:
        _fail(
            "SSRF_BLOCKED",
            "Target host resolution failed during SSRF policy enforcement",
            {
                "host": host,
                "reason_code": "ssrf_resolution_failed",
                "error_type": type(exc).__name__,
            },
        )

    for row in resolved:
        sockaddr = row[4]
        if not isinstance(sockaddr, tuple) or not sockaddr:
            continue
        candidate = str(sockaddr[0] or "").strip()
        if not candidate:
            continue
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            _fail(
                "SSRF_BLOCKED",
                "Target host resolved to a private/loopback address",
                {"host": host, "resolved_ip": str(ip)},
            )
    return parsed


def resolve_allow_private_hosts(
    request: dict[str, Any] | None, ctx: Any | None
) -> bool:
    """Derive the effective `allow_private_hosts` flag for a request."""

    policy_cfg = extract_fetch_policy(ctx)
    allow_private_hosts = bool(policy_cfg.get("allow_private_hosts", False))
    if isinstance(request, dict) and bool(request.get("allow_private_hosts", False)):
        allow_private_hosts = True
    return allow_private_hosts


__all__ = [
    "FetchPolicyError",
    "extract_fetch_policy",
    "enforce_url_policy",
    "resolve_allow_private_hosts",
]
