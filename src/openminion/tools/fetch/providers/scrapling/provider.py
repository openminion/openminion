from typing import Any

from openminion.tools.fetch.providers.core_http import provider as core_http_provider

from .interfaces import FetchProviderProtocol, ProviderCapabilities
from .schemas import ScraplingProviderOptions


class ScraplingFetchProvider(FetchProviderProtocol):
    """Reference scrapling provider implementation for TOOL-005.

    V1 keeps OpenMinion policy + SSRF boundaries in core fetch and allows this
    provider to resolve mode semantics with deterministic fallback behavior.
    """

    name = "scrapling"
    capabilities: ProviderCapabilities = {
        "render": ["none", "dom"],
        "extract": ["auto", "readability_like", "selector", "raw_text", "json"],
        "anti_bot": ["none", "stealth"],
        "concurrency": ["max_pages", "session_pool"],
    }

    def fetch(self, request: dict[str, Any], ctx: Any | None = None) -> dict[str, Any]:
        policy_cfg = _resolve_policy_config(ctx)
        provider_opts = request.get("provider_options", {})
        scrapling_opts_payload = (
            provider_opts.get("scrapling", {})
            if isinstance(provider_opts, dict)
            else {}
        )
        try:
            opts = ScraplingProviderOptions.model_validate(scrapling_opts_payload)
        except Exception as exc:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": f"invalid scrapling provider options: {exc}",
                },
                "backend": "scrapling",
            }

        mode = str(opts.mode or "auto").strip().lower()
        effective_mode = "static" if mode == "auto" else mode

        if effective_mode == "dynamic" and not bool(
            policy_cfg.get("allow_dynamic", False)
        ):
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "dynamic scrapling mode requires tool.fetch.browser authorization",
                    "details": {"required_scope": "tool.fetch.browser"},
                },
                "backend": "scrapling",
            }
        if effective_mode == "stealth" and not bool(
            policy_cfg.get("allow_stealth", False)
        ):
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "stealth scrapling mode requires tool.fetch.stealth authorization",
                    "details": {"required_scope": "tool.fetch.stealth"},
                },
                "backend": "scrapling",
            }
        if opts.geoip and not bool(policy_cfg.get("allow_geoip", False)):
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "geoip option requires tool.fetch.geoip authorization",
                    "details": {"required_scope": "tool.fetch.geoip"},
                },
                "backend": "scrapling",
            }

        downgraded = False
        if effective_mode in {"dynamic", "stealth"}:
            # V1 reference adapter keeps behavior deterministic by downgrading
            # to static mode when advanced browser stack is unavailable.
            effective_mode = "static"
            downgraded = True

        delegated_request = dict(request)
        delegated_request["prefer_backend"] = "core-http"
        result = core_http_provider.fetch(delegated_request, ctx)

        warnings: list[str] = []
        if isinstance(result, dict):
            raw_warnings = result.get("warnings", [])
            if isinstance(raw_warnings, list):
                warnings.extend(str(item) for item in raw_warnings if str(item).strip())
            if downgraded:
                warnings.append("DOWNGRADED_TO_STATIC")
            result["warnings"] = warnings
            result["backend"] = f"scrapling:{effective_mode}"
            return result

        return {
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "scrapling provider returned invalid delegate payload",
            },
            "backend": "scrapling",
        }


def _resolve_policy_config(ctx: Any | None) -> dict[str, Any]:
    if ctx is None:
        return {}
    policy = getattr(ctx, "policy", None)
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, dict):
        return {}
    tools_cfg = raw.get("tools", {})
    if not isinstance(tools_cfg, dict):
        return {}
    cfg = tools_cfg.get("fetch_scrapling", {})
    return dict(cfg) if isinstance(cfg, dict) else {}


provider = ScraplingFetchProvider()

__all__ = ["ScraplingFetchProvider", "provider"]
