import ipaddress
import re
import socket
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .config import SKILL_URL_FETCH_TIMEOUT_SECONDS
from .constants import (
    SKILL_URL_FETCH_USER_AGENT,
    SKILL_URL_MAX_CONTENT_BYTES,
    SKILL_URL_MAX_CONTENT_CHARS,
    SKILL_URL_MAX_REDIRECTS,
)
from .inspect import scan


class _NoFollowRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _resolve_host_ips(host: str) -> set[str]:
    """SIPS-04: resolve a host to its set of IP addresses.

    Returns an empty set on resolution failure so the caller can treat
    "couldn't resolve" identically to "resolved differently" — both are
    suspicious states for the DNS rebinding guard.
    """
    if not host:
        return set()
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return set()
    addrs: set[str] = set()
    for info in infos:
        if len(info) > 4 and info[4]:
            addr = str(info[4][0] or "").strip()
            if addr:
                addrs.add(addr)
    return addrs


def is_blocked_skill_host(host: str) -> bool:
    normalized = host.lower().strip()

    if normalized in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True

    if normalized.endswith(".local") or normalized.startswith("169.254."):
        return True

    if any(
        normalized.endswith(f".{tld}") for tld in {"internal", "corp", "home", "lan"}
    ):
        return True

    try:
        ip = ipaddress.ip_address(normalized)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(normalized, None)
        for info in infos:
            addr = info[4][0] if len(info) > 4 and info[4] else ""
            try:
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback:
                    return True
            except ValueError:
                continue
    except Exception:
        pass

    return False


def is_valid_markdown_content(content: str) -> bool:
    if not content or len(content) < 50:
        return False

    text = content[:5000]
    indicator_count = 0
    if "# " in text or text.startswith("#"):
        indicator_count += 1
    if "## " in text or "### " in text:
        indicator_count += 1
    if "- " in text or "* " in text:
        indicator_count += 1
    if "```" in text:
        indicator_count += 1
    if "[" in text and "]" in text:
        indicator_count += 1
    if "|" in text and "---" in text:
        indicator_count += 1

    return indicator_count >= 2 or "# " in text or text.startswith("#")


def extract_skill_name_from_url(url: str) -> str:
    parsed = urllib_parse.urlparse(url)
    path = parsed.path or "skill"

    filename = path.split("/")[-1] if "/" in path else path
    name = filename.replace(".md", "")

    name = re.sub(
        r"(?<![a-zA-Z0-9_-])SKILL(?![a-zA-Z0-9_-])", "", name, flags=re.IGNORECASE
    )
    name = re.sub(
        r"(?<![a-zA-Z0-9_-])skill(?![a-zA-Z0-9_-])", "", name, flags=re.IGNORECASE
    )
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
    name = re.sub(r"_+", "_", name)

    if not name:
        name = "imported_skill"

    return name


def _validate_fetch_target(
    current_url: str,
    *,
    baseline_ips: set[str] | None,
) -> tuple[str | None, dict[str, Any] | None, set[str]]:
    """Return (host, error_dict_or_None, resolved_ips).

    Used at every redirect hop. Re-checks scheme, host blocklist, and (if
    baseline_ips is provided) the SIPS-04 DNS rebinding guard. Caller
    proceeds with the fetch when error_dict is None.
    """
    parsed = urllib_parse.urlparse(current_url)
    if parsed.scheme not in {"http", "https"}:
        return (
            None,
            {
                "ok": False,
                "error_code": "INVALID_SCHEME",
                "error_message": (
                    f"URL scheme must be http/https, got: {parsed.scheme}"
                ),
            },
            set(),
        )

    host = parsed.hostname or ""
    if is_blocked_skill_host(host):
        return (
            host,
            {
                "ok": False,
                "error_code": "BLOCKED_HOST",
                "error_message": (
                    "URL host is blocked (private/local addresses not allowed)"
                ),
            },
            set(),
        )

    resolved_ips = _resolve_host_ips(host)
    if baseline_ips is not None and resolved_ips != baseline_ips:
        return (
            host,
            {
                "ok": False,
                "error_code": "URL_INGEST_DNS_REBINDING_GUARD",
                "error_message": (
                    f"Host {host!r} resolved to different IPs between check "
                    "and fetch; refusing as a DNS rebinding guard."
                ),
            },
            resolved_ips,
        )

    return host, None, resolved_ips


def _redirect_limit_error() -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": "URL_INGEST_REDIRECT_LIMIT",
        "error_message": (
            f"Redirect chain exceeded {SKILL_URL_MAX_REDIRECTS} hops; refusing."
        ),
    }


def _fetch_request(current_url: str) -> urllib_request.Request:
    return urllib_request.Request(
        current_url,
        headers={"User-Agent": SKILL_URL_FETCH_USER_AGENT},
        method="GET",
    )


def _redirect_target(
    *,
    current_url: str,
    location: str | None,
    hop: int,
) -> tuple[str | None, dict[str, Any] | None]:
    if not location:
        return None, {
            "ok": False,
            "error_code": "FETCH_HTTP_ERROR",
            "error_message": "HTTP redirect without Location header",
        }
    if hop >= SKILL_URL_MAX_REDIRECTS:
        return None, _redirect_limit_error()
    return urllib_parse.urljoin(current_url, location), None


def _markdown_fetch_success(
    *, raw: bytes, content_type: str, current_url: str, original_url: str
) -> dict[str, Any]:
    truncated = False
    if len(raw) > SKILL_URL_MAX_CONTENT_BYTES:
        raw = raw[:SKILL_URL_MAX_CONTENT_BYTES]
        truncated = True

    content = raw.decode("utf-8", errors="replace")
    if len(content) > SKILL_URL_MAX_CONTENT_CHARS:
        content = content[:SKILL_URL_MAX_CONTENT_CHARS]
        truncated = True

    if not is_valid_markdown_content(content):
        return {
            "ok": False,
            "error_code": "INVALID_MARKDOWN",
            "error_message": "Fetched content does not appear to be valid markdown",
        }

    return {
        "ok": True,
        "content": content,
        "content_length": len(content),
        "content_type": content_type,
        "truncated": truncated,
        "suggested_name": extract_skill_name_from_url(original_url),
        "final_url": current_url,
    }


def _handle_fetch_http_error(
    exc: urllib_error.HTTPError, *, current_url: str, hop: int
) -> tuple[str | None, dict[str, Any] | None]:
    if exc.code in (301, 302, 303, 307, 308):
        location = exc.headers.get("Location") if exc.headers else None
        return _redirect_target(current_url=current_url, location=location, hop=hop)
    return None, {
        "ok": False,
        "error_code": "FETCH_HTTP_ERROR",
        "error_message": f"HTTP {exc.code}: {exc.reason}",
    }


def fetch_skill_markdown_from_url(url: str) -> dict[str, Any]:
    """Fetch skill markdown from url helper."""
    path = urllib_parse.urlparse(url).path or ""
    if not path.lower().endswith(".md"):
        return {
            "ok": False,
            "error_code": "INVALID_FILE_TYPE",
            "error_message": "URL path must end with .md extension",
        }

    # initial DNS resolution baseline. The same host must resolve
    # to the same IP set when we actually fetch, otherwise we treat the
    # difference as a DNS rebinding signal.
    initial_host, initial_error, baseline_ips = _validate_fetch_target(
        url, baseline_ips=None
    )
    if initial_error is not None:
        return initial_error

    current_url = url
    opener = urllib_request.build_opener(_NoFollowRedirectHandler())

    for hop in range(SKILL_URL_MAX_REDIRECTS + 1):
        host, target_error, _ips = _validate_fetch_target(
            current_url, baseline_ips=baseline_ips if hop == 0 else None
        )
        if target_error is not None:
            return target_error
        del host  # variable not used downstream; validated above

        try:
            req = _fetch_request(current_url)
            with opener.open(
                req,
                timeout=float(SKILL_URL_FETCH_TIMEOUT_SECONDS),
            ) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                location = resp.headers.get("Location") if resp.headers else None
                if status in (301, 302, 303, 307, 308) and location:
                    new_url, redirect_error = _redirect_target(
                        current_url=current_url, location=location, hop=hop
                    )
                    if redirect_error is not None:
                        return redirect_error
                    assert new_url is not None
                    current_url = new_url
                    continue

                content_type = str(resp.headers.get("Content-Type", "unknown"))
                raw = resp.read(SKILL_URL_MAX_CONTENT_BYTES + 1)
        except urllib_error.HTTPError as exc:
            new_url, fetch_error = _handle_fetch_http_error(
                exc, current_url=current_url, hop=hop
            )
            if fetch_error is not None:
                return fetch_error
            if new_url is not None:
                current_url = new_url
                continue
        except urllib_error.URLError as exc:
            return {
                "ok": False,
                "error_code": "FETCH_URL_ERROR",
                "error_message": f"URL error: {exc.reason}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "FETCH_EXCEPTION",
                "error_message": f"Exception during fetch: {str(exc)}",
            }

        return _markdown_fetch_success(
            raw=raw,
            content_type=content_type,
            current_url=current_url,
            original_url=url,
        )

    # Loop should always return inside; this is defensive.
    return _redirect_limit_error()


def ingest_skill_url(
    skill_api: Any,
    *,
    url: str,
    name: str | None = None,
    scope: str = "global",
    max_snippet_tokens: int = 500,
    enforce_safety: bool = True,
    trust: str | None = None,
) -> dict[str, Any]:
    fetch_result = fetch_skill_markdown_from_url(url)
    if not fetch_result["ok"]:
        return {
            "ok": False,
            "error": {
                "code": str(fetch_result["error_code"]),
                "message": str(fetch_result["error_message"]),
            },
            "source_type": "url",
            "source_url": url,
        }

    resolved_name = str(name or "").strip() or str(fetch_result["suggested_name"])
    markdown = str(fetch_result["content"])

    risk_level, issues = scan(markdown)
    safe = risk_level != "critical"
    if enforce_safety and not safe:
        return {
            "ok": False,
            "error": {
                "code": "SAFETY_REJECTED",
                "message": "Skill ingest blocked due to critical safety findings.",
            },
            "risk_level": risk_level,
            "safe": False,
            "issues": issues,
            "safety_enforced": enforce_safety,
            "source_type": "url",
            "source_url": url,
            "name": resolved_name,
            "content_type": fetch_result.get("content_type"),
            "content_length": fetch_result.get("content_length"),
            "truncated": fetch_result.get("truncated"),
        }

    try:
        skill_id, version_hash, warnings = skill_api.ingest_url(
            url=url,
            name=resolved_name,
            markdown=markdown,
            scope=scope,
            trust=trust,
            promotion_path="runtime",
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "INGEST_FAILED",
                "message": str(exc),
            },
            "source_type": "url",
            "source_url": url,
            "name": resolved_name,
        }

    snippet = ""
    snippet_hash = ""
    try:
        snippet, snippet_hash = skill_api.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=max_snippet_tokens,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "skill_id": skill_id,
        "version_hash": version_hash,
        "snippet": snippet,
        "snippet_hash": snippet_hash,
        "warnings": list(warnings or []),
        "risk_level": risk_level,
        "safe": safe,
        "issues": issues,
        "safety_enforced": enforce_safety,
        "source_type": "url",
        "source_url": url,
        "name": resolved_name,
        "content_type": fetch_result.get("content_type"),
        "content_length": fetch_result.get("content_length"),
        "truncated": fetch_result.get("truncated"),
    }


__all__ = [
    "extract_skill_name_from_url",
    "fetch_skill_markdown_from_url",
    "ingest_skill_url",
    "is_blocked_skill_host",
    "is_valid_markdown_content",
]
