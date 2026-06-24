from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .models import BrowserCallArgs, TabInfo
from .providers import BrowserProvider, BrowserProviderContext
from .session_state import SessionBrowserState
from .payloads import is_meaningful_url


StateLookup = Callable[[str, Any], SessionBrowserState]
ExtractTabs = Callable[[Any], list[TabInfo]]
IsStaleRecoverableError = Callable[[Exception], bool]


@dataclass
class BrowserTabResolver:
    state_lookup: StateLookup
    extract_tabs: ExtractTabs
    is_stale_recoverable_error: IsStaleRecoverableError

    def resolve_tab(
        self,
        *,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
        prefer_url: bool = False,
    ) -> tuple[str, TabInfo | None, dict[str, Any]]:
        state = self.state_lookup(provider.provider_id, provider_ctx)
        instance_id = str(call.instance_id or state.instance_id or "").strip() or None
        details: dict[str, Any] = {
            "prefer_url": bool(prefer_url),
            "instance_id": str(instance_id or ""),
        }
        try:
            payload = provider.tab_list(provider_ctx, instance_id=instance_id)
        except Exception as exc:
            details["strategy"] = "tab_list_error"
            details["error"] = f"{type(exc).__name__}: {exc}"
            details["stale_context_hint"] = self.is_stale_recoverable_error(exc)
            return "", None, details
        tabs = self.extract_tabs(payload)
        details["tab_count"] = len(tabs)
        if not tabs:
            details["strategy"] = "no_tabs"
            return "", None, details

        options = call.options if isinstance(call.options, Mapping) else {}
        candidates, filter_meta = self.filter_tabs(tabs, options=options)
        details.update(filter_meta)
        details["filtered_count"] = len(candidates)
        if not candidates:
            details["strategy"] = "no_match_after_filter"
            return "", None, details

        if prefer_url and call.url:
            target_url = str(call.url).strip().lower()
            for tab in candidates:
                if tab.url.lower() == target_url:
                    details["strategy"] = "prefer_url_exact"
                    return tab.id, tab, details
            target_host = self._host(target_url)
            if target_host:
                for tab in candidates:
                    if self._host(tab.url.lower()) == target_host:
                        details["strategy"] = "prefer_url_host"
                        return tab.id, tab, details
            for tab in candidates:
                if target_url and target_url in tab.url.lower():
                    details["strategy"] = "prefer_url_substring"
                    return tab.id, tab, details

        if len(candidates) == 1:
            details["strategy"] = "single_candidate"
            return candidates[0].id, candidates[0], details

        reuse_existing = self._as_bool(options.get("reuse_existing"), default=True)
        reuse_policy = (
            str(options.get("reuse_policy", "single_non_blank")).strip().lower()
        )
        details["reuse_existing"] = reuse_existing
        details["reuse_policy"] = reuse_policy
        if reuse_existing:
            meaningful = [tab for tab in candidates if self._is_meaningful_tab(tab)]
            if reuse_policy == "single_non_blank":
                if len(meaningful) == 1:
                    details["strategy"] = "single_non_blank"
                    return meaningful[0].id, meaningful[0], details
            elif reuse_policy == "first_non_blank":
                if meaningful:
                    details["strategy"] = "first_non_blank"
                    return meaningful[0].id, meaningful[0], details
            elif reuse_policy == "first_any":
                details["strategy"] = "first_any"
                return candidates[0].id, candidates[0], details

        details["strategy"] = "ambiguous"
        details["candidate_ids"] = [tab.id for tab in candidates[:20]]
        return "", None, details

    def filter_tabs(
        self,
        tabs: list[TabInfo],
        *,
        options: Mapping[str, Any],
    ) -> tuple[list[TabInfo], dict[str, Any]]:
        candidates = list(tabs)
        details: dict[str, Any] = {}

        url_contains = str(options.get("tab_url_contains", "")).strip().lower()
        if url_contains:
            details["tab_url_contains"] = url_contains
            candidates = [tab for tab in candidates if url_contains in tab.url.lower()]

        title_contains = str(options.get("tab_title_contains", "")).strip().lower()
        if title_contains:
            details["tab_title_contains"] = title_contains
            candidates = [
                tab for tab in candidates if title_contains in tab.title.lower()
            ]

        tab_id_contains = str(options.get("tab_id_contains", "")).strip().lower()
        if tab_id_contains:
            details["tab_id_contains"] = tab_id_contains
            candidates = [
                tab for tab in candidates if tab_id_contains in tab.id.lower()
            ]

        index_raw = options.get("tab_index")
        if isinstance(index_raw, int):
            details["tab_index"] = index_raw
            if index_raw < 0 or index_raw >= len(candidates):
                return [], details
            return [candidates[index_raw]], details

        return candidates, details

    def _as_bool(self, value: Any, *, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        token = str(value).strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        return default

    def _is_meaningful_tab(self, tab: TabInfo) -> bool:
        return is_meaningful_url(tab.url)

    def _host(self, value: str) -> str:
        token = str(value or "").strip()
        if not token:
            return ""
        parsed = urlparse(token)
        return str(parsed.netloc or "").lower()
