from typing import Any

from openminion.tools.fetch.providers.core_http import provider as core_http_provider

from .interfaces import FetchProviderProtocol, ProviderCapabilities
from .schemas import ScraplingProviderOptions
from .constants import (
    FETCH_BACKEND_AUTO,
    FETCH_EXTRACT_MODE_NONE,
    FETCH_PROVIDER_ID_CORE_HTTP,
    FETCH_SCRAPLING_MODE_DYNAMIC,
    FETCH_SCRAPLING_MODE_STATIC,
    FETCH_SCRAPLING_MODE_STEALTH,
    FETCH_SCRAPLING_PROVIDER_ID,
)


class ScraplingFetchProvider(FetchProviderProtocol):
    """Reference scrapling provider implementation for TOOL-005.

    V1 keeps OpenMinion policy + SSRF boundaries in core fetch and allows this
    provider to resolve mode semantics with deterministic fallback behavior.
    """

    name = FETCH_SCRAPLING_PROVIDER_ID
    capabilities: ProviderCapabilities = {
        "render": [FETCH_EXTRACT_MODE_NONE, "dom"],
        "extract": [
            FETCH_BACKEND_AUTO,
            "readability_like",
            "selector",
            "raw_text",
            "json",
        ],
        "anti_bot": [FETCH_EXTRACT_MODE_NONE, FETCH_SCRAPLING_MODE_STEALTH],
        "concurrency": ["max_pages", "session_pool"],
    }

    def fetch(self, request: dict[str, Any], ctx: Any | None = None) -> dict[str, Any]:
        policy_cfg = _resolve_policy_config(ctx)
        provider_opts = request.get("provider_options", {})
        scrapling_opts_payload = (
            provider_opts.get(FETCH_SCRAPLING_PROVIDER_ID, {})
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
                "backend": FETCH_SCRAPLING_PROVIDER_ID,
            }

        mode = str(opts.mode or FETCH_BACKEND_AUTO).strip().lower()
        effective_mode = (
            FETCH_SCRAPLING_MODE_STATIC if mode == FETCH_BACKEND_AUTO else mode
        )

        if effective_mode == FETCH_SCRAPLING_MODE_DYNAMIC and not bool(
            policy_cfg.get("allow_dynamic", False)
        ):
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "dynamic scrapling mode requires tool.fetch.browser authorization",
                    "details": {"required_scope": "tool.fetch.browser"},
                },
                "backend": FETCH_SCRAPLING_PROVIDER_ID,
            }
        if effective_mode == FETCH_SCRAPLING_MODE_STEALTH and not bool(
            policy_cfg.get("allow_stealth", False)
        ):
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "stealth scrapling mode requires tool.fetch.stealth authorization",
                    "details": {"required_scope": "tool.fetch.stealth"},
                },
                "backend": FETCH_SCRAPLING_PROVIDER_ID,
            }
        if opts.geoip and not bool(policy_cfg.get("allow_geoip", False)):
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "geoip option requires tool.fetch.geoip authorization",
                    "details": {"required_scope": "tool.fetch.geoip"},
                },
                "backend": FETCH_SCRAPLING_PROVIDER_ID,
            }

        downgraded = False
        if effective_mode in {
            FETCH_SCRAPLING_MODE_DYNAMIC,
            FETCH_SCRAPLING_MODE_STEALTH,
        }:
            # V1 reference adapter keeps behavior deterministic by downgrading
            # to static mode when advanced browser stack is unavailable.
            effective_mode = FETCH_SCRAPLING_MODE_STATIC
            downgraded = True

        delegated_request = dict(request)
        delegated_request["prefer_backend"] = FETCH_PROVIDER_ID_CORE_HTTP
        result = core_http_provider.fetch(delegated_request, ctx)

        warnings: list[str] = []
        if isinstance(result, dict):
            raw_warnings = result.get("warnings", [])
            if isinstance(raw_warnings, list):
                warnings.extend(str(item) for item in raw_warnings if str(item).strip())
            if downgraded:
                warnings.append("DOWNGRADED_TO_STATIC")
            result["warnings"] = warnings
            result["backend"] = f"{FETCH_SCRAPLING_PROVIDER_ID}:{effective_mode}"
            return result

        return {
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "scrapling provider returned invalid delegate payload",
            },
            "backend": FETCH_SCRAPLING_PROVIDER_ID,
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
