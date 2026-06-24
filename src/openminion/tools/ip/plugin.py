"""IP lookup tool plugin."""

import ipaddress
import json
import socket
from collections.abc import Mapping
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from openminion.tools.config import resolve_tool_context_env
from openminion.tools.env import (
    get_ip_public_lookup_endpoints,
    get_ip_public_timeout_seconds,
)

from .constants import DEFAULT_IP_PROVIDER_ID
from .interfaces import TOOL_IP_LOCAL, TOOL_IP_PUBLIC
from .providers import provider_registry, register_provider
from .schemas import IpLocalArgs, IpPublicArgs


class _BuiltinIpProvider:
    provider_id = DEFAULT_IP_PROVIDER_ID

    def resolve_public(
        self,
        *,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> Mapping[str, Any]:
        return _builtin_public(dict(args), ctx)

    def resolve_local(
        self,
        *,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> Mapping[str, Any]:
        return _builtin_local(dict(args), ctx)

    def healthcheck(self) -> bool:
        return True


def _ensure_default_provider_registered() -> None:
    registry = provider_registry()
    if registry.get(DEFAULT_IP_PROVIDER_ID) is not None:
        return
    register_provider(_BuiltinIpProvider())


def _error(
    code: str,
    message: str,
    *,
    method: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": str(code),
            "message": str(message),
            "details": dict(details or {}),
        },
        "data": {
            "source": "openminion-tool-ip",
            "method": method,
            "reason_code": str(code).lower(),
        },
    }


def _policy_flag(cfg: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _ip_policy_config(ctx: Any) -> dict[str, Any]:
    raw = getattr(getattr(ctx, "policy", None), "raw", {})
    if not isinstance(raw, Mapping):
        return {}
    tools_cfg = raw.get("tools")
    if not isinstance(tools_cfg, Mapping):
        return {}
    ip_cfg = tools_cfg.get("ip")
    return dict(ip_cfg) if isinstance(ip_cfg, Mapping) else {}


def _lookup_urls(cfg: Mapping[str, Any], *, ctx: Any) -> tuple[str, ...]:
    raw = cfg.get("public_lookup_urls")
    if isinstance(raw, str):
        parsed = tuple(token.strip() for token in raw.split(",") if token.strip())
        if parsed:
            return parsed
    if isinstance(raw, list):
        parsed = tuple(str(token).strip() for token in raw if str(token).strip())
        if parsed:
            return parsed
    return get_ip_public_lookup_endpoints(env=resolve_tool_context_env(ctx))


def _lookup_timeout_seconds(
    cfg: Mapping[str, Any],
    args: Mapping[str, Any],
    *,
    ctx: Any,
) -> float:
    timeout_ms = args.get("timeout_ms")
    if timeout_ms is None:
        timeout_ms = cfg.get("public_timeout_ms")
    if timeout_ms is None:
        return get_ip_public_timeout_seconds(env=resolve_tool_context_env(ctx))
    try:
        value = int(timeout_ms)
    except (TypeError, ValueError):
        return get_ip_public_timeout_seconds(env=resolve_tool_context_env(ctx))
    return max(0.25, min(float(value) / 1000.0, 30.0))


def _extract_ip_candidate(payload_text: str) -> str | None:
    token = str(payload_text or "").strip()
    if not token:
        return None

    try:
        payload = json.loads(token)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, Mapping):
        candidate = str(payload.get("ip", "")).strip()
        if candidate:
            return candidate

    for chunk in token.replace("\n", " ").split():
        candidate = chunk.strip().strip("[](),;\"'")
        if candidate:
            return candidate
    return None


def _parse_ip(candidate: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        parsed = ipaddress.ip_address(str(candidate or "").strip())
    except ValueError:
        return None
    return parsed


def _fetch_public_ip(url: str, *, timeout_seconds: float) -> str | None:
    req = urllib_request.Request(
        url=str(url),
        headers={
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "openminion-ip-tool/1.0",
        },
    )
    with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
    return _extract_ip_candidate(body)


def _builtin_public(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    cfg = _ip_policy_config(ctx)
    if not _policy_flag(cfg, "enabled", default=True):
        return _error(
            "POLICY_DENIED", "ip tool is disabled by policy", method=TOOL_IP_PUBLIC
        )
    if not _policy_flag(cfg, "public_enabled", default=True):
        return _error(
            "POLICY_DENIED",
            "public ip lookup is disabled by policy",
            method=TOOL_IP_PUBLIC,
        )

    lookup_urls = _lookup_urls(cfg, ctx=ctx)
    if not lookup_urls:
        return _error(
            "CONFIG_ERROR",
            "No public IP lookup endpoints configured",
            method=TOOL_IP_PUBLIC,
            details={"key": "tools.ip.public_lookup_urls"},
        )
    timeout_seconds = _lookup_timeout_seconds(cfg, args, ctx=ctx)

    warnings: list[str] = []
    saw_private_or_local = False
    for url in lookup_urls:
        try:
            candidate = _fetch_public_ip(url, timeout_seconds=timeout_seconds)
        except (TimeoutError, urllib_error.URLError, urllib_error.HTTPError) as exc:
            warnings.append(f"{url}: {exc.__class__.__name__}")
            continue
        except Exception as exc:
            warnings.append(f"{url}: {exc.__class__.__name__}")
            continue

        parsed = _parse_ip(str(candidate or ""))
        if parsed is None:
            warnings.append(f"{url}: invalid ip payload")
            continue
        if not parsed.is_global:
            saw_private_or_local = True
            warnings.append(f"{url}: non-public ip returned")
            continue

        ip_text = str(parsed)
        return {
            "ok": True,
            "content": f"Public IP: {ip_text}",
            "data": {
                "source": "openminion-tool-ip",
                "method": TOOL_IP_PUBLIC,
                "ip": ip_text,
                "version": int(parsed.version),
                "lookup_url": str(url),
                "timeout_seconds": float(timeout_seconds),
                "warnings": list(warnings),
            },
            "warnings": list(warnings),
            "verified": True,
        }

    if saw_private_or_local:
        return _error(
            "NON_PUBLIC_IP",
            "Public IP providers returned only private/local addresses",
            method=TOOL_IP_PUBLIC,
            details={"endpoints": list(lookup_urls), "warnings": warnings},
        )
    return _error(
        "PUBLIC_IP_UNAVAILABLE",
        "Unable to resolve public IP from configured providers",
        method=TOOL_IP_PUBLIC,
        details={"endpoints": list(lookup_urls), "warnings": warnings},
    )


def _scope_label(parsed: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_link_local:
        return "link_local"
    if parsed.is_private:
        return "private"
    if parsed.is_global:
        return "public"
    if parsed.is_multicast:
        return "multicast"
    if parsed.is_reserved:
        return "reserved"
    return "other"


def _collect_local_candidates() -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    discovered: dict[str, ipaddress.IPv4Address | ipaddress.IPv6Address] = {}

    for host in (socket.gethostname(), "localhost"):
        try:
            rows = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            rows = []
        for row in rows:
            sockaddr = row[4] if len(row) > 4 else None
            if not isinstance(sockaddr, tuple) or not sockaddr:
                continue
            candidate = str(sockaddr[0] or "").strip()
            parsed = _parse_ip(candidate)
            if parsed is None:
                continue
            discovered[str(parsed)] = parsed

    udp_probes: tuple[tuple[int, tuple[Any, ...]], ...] = (
        (socket.AF_INET, ("8.8.8.8", 53)),
        (socket.AF_INET6, ("2001:4860:4860::8888", 53, 0, 0)),
    )
    for family, target in udp_probes:
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
            sock.connect(target)
            local_ip = str(sock.getsockname()[0] or "").strip()
            parsed = _parse_ip(local_ip)
            if parsed is not None:
                discovered[str(parsed)] = parsed
        except OSError:
            continue
        finally:
            if sock is not None:
                sock.close()

    return list(discovered.values())


def _builtin_local(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    cfg = _ip_policy_config(ctx)
    if not _policy_flag(cfg, "enabled", default=True):
        return _error(
            "POLICY_DENIED", "ip tool is disabled by policy", method=TOOL_IP_LOCAL
        )
    if not _policy_flag(cfg, "local_enabled", default=True):
        return _error(
            "POLICY_DENIED",
            "local ip lookup is disabled by policy",
            method=TOOL_IP_LOCAL,
        )

    include_loopback = bool(args.get("include_loopback", False))
    candidates = _collect_local_candidates()
    if not include_loopback:
        candidates = [item for item in candidates if not item.is_loopback]

    if not candidates:
        return _error(
            "LOCAL_IP_UNAVAILABLE",
            "No local IP addresses are available",
            method=TOOL_IP_LOCAL,
        )

    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.is_private else 1,
            0 if item.is_global else 1,
            1 if item.is_loopback else 0,
            str(item),
        ),
    )
    primary = ordered[0]
    addresses = [
        {"ip": str(item), "version": int(item.version), "scope": _scope_label(item)}
        for item in ordered
    ]
    return {
        "ok": True,
        "content": f"Local IP: {primary}",
        "data": {
            "source": "openminion-tool-ip",
            "method": TOOL_IP_LOCAL,
            "primary_ip": str(primary),
            "addresses": addresses,
            "include_loopback": include_loopback,
        },
        "verified": True,
    }


def _merge_chain_warnings(
    payload: dict[str, Any], warnings: list[str]
) -> dict[str, Any]:
    if not warnings:
        return payload
    existing = payload.get("warnings")
    merged = [str(item) for item in existing] if isinstance(existing, list) else []
    merged.extend(warnings)
    payload["warnings"] = merged
    return payload


def _dispatch_public(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    _ensure_default_provider_registered()
    registry = provider_registry()
    chain_warnings: list[str] = []
    last_non_ok: dict[str, Any] | None = None

    for provider_id in registry.list_provider_ids():
        provider = registry.get(provider_id)
        if provider is None:
            continue

        healthcheck = getattr(provider, "healthcheck", None)
        if callable(healthcheck):
            try:
                if not bool(healthcheck()):
                    chain_warnings.append(
                        f"provider '{provider_id}' reported unhealthy"
                    )
                    continue
            except Exception as exc:
                chain_warnings.append(
                    f"provider '{provider_id}' healthcheck failed: {exc}"
                )
                continue

        try:
            payload = provider.resolve_public(args=args, ctx=ctx)
        except Exception as exc:
            chain_warnings.append(f"provider '{provider_id}' failed: {exc}")
            continue

        if not isinstance(payload, Mapping):
            chain_warnings.append(f"provider '{provider_id}' returned invalid payload")
            continue

        result = dict(payload)
        if bool(result.get("ok", False)):
            return _merge_chain_warnings(result, chain_warnings)
        chain_warnings.append(f"provider '{provider_id}' returned non-ok payload")
        last_non_ok = result

    if last_non_ok is not None:
        return _merge_chain_warnings(last_non_ok, chain_warnings)
    return _error(
        "PUBLIC_IP_UNAVAILABLE",
        "Unable to resolve public IP from provider chain",
        method=TOOL_IP_PUBLIC,
        details={"warnings": chain_warnings},
    )


def _dispatch_local(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    _ensure_default_provider_registered()
    registry = provider_registry()
    chain_warnings: list[str] = []
    last_non_ok: dict[str, Any] | None = None

    for provider_id in registry.list_provider_ids():
        provider = registry.get(provider_id)
        if provider is None:
            continue

        healthcheck = getattr(provider, "healthcheck", None)
        if callable(healthcheck):
            try:
                if not bool(healthcheck()):
                    chain_warnings.append(
                        f"provider '{provider_id}' reported unhealthy"
                    )
                    continue
            except Exception as exc:
                chain_warnings.append(
                    f"provider '{provider_id}' healthcheck failed: {exc}"
                )
                continue

        try:
            payload = provider.resolve_local(args=args, ctx=ctx)
        except Exception as exc:
            chain_warnings.append(f"provider '{provider_id}' failed: {exc}")
            continue

        if not isinstance(payload, Mapping):
            chain_warnings.append(f"provider '{provider_id}' returned invalid payload")
            continue

        result = dict(payload)
        if bool(result.get("ok", False)):
            return _merge_chain_warnings(result, chain_warnings)
        chain_warnings.append(f"provider '{provider_id}' returned non-ok payload")
        last_non_ok = result

    if last_non_ok is not None:
        return _merge_chain_warnings(last_non_ok, chain_warnings)
    return _error(
        "LOCAL_IP_UNAVAILABLE",
        "Unable to resolve local IP from provider chain",
        method=TOOL_IP_LOCAL,
        details={"warnings": chain_warnings},
    )


def _h_public(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch_public(dict(args or {}), ctx)


def _h_local(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch_local(dict(args or {}), ctx)


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=TOOL_IP_PUBLIC,
            args_model=IpPublicArgs,
            min_scope="READ_ONLY",
            handler=_h_public,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "ip"),
            capabilities=("read_only", "network"),
        )
    )
    registry.add(
        ToolSpec(
            name=TOOL_IP_LOCAL,
            args_model=IpLocalArgs,
            min_scope="READ_ONLY",
            handler=_h_local,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "ip"),
            capabilities=("read_only", "network"),
        )
    )


__all__ = ["register", "register_provider"]
