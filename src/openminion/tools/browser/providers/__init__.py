import logging
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol
from collections.abc import Callable, Mapping

from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.config import resolve_provider_register_hook as _resolve_hook

from ..models import (
    BrowserAction,
    BrowserCapabilities,
    BrowserResult,
    InstanceSpec,
    NavigateOptions,
    OutputOptions,
    SnapshotOptions,
    TextOptions,
)


@dataclass(frozen=True)
class BrowserProviderContext:
    tool_context: RuntimeContext | None = None
    workspace_root: str = ""
    trace_id: str = ""
    session_id: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict)


class BrowserProvider(Protocol):
    provider_id: str
    capabilities: BrowserCapabilities
    provider_version: str

    def resource_selectors(self, args: Mapping[str, Any]) -> ResourceSelectors: ...

    def ensure_ready(self, ctx: BrowserProviderContext) -> Mapping[str, Any]: ...

    def instance_start(
        self, ctx: BrowserProviderContext, spec: InstanceSpec
    ) -> Mapping[str, Any]: ...

    def instance_list(
        self, ctx: BrowserProviderContext
    ) -> list[Mapping[str, Any]] | Mapping[str, Any]: ...

    def instance_stop(
        self, ctx: BrowserProviderContext, instance_id: str
    ) -> Mapping[str, Any] | None: ...

    def instance_kill(
        self, ctx: BrowserProviderContext, instance_id: str
    ) -> Mapping[str, Any] | None: ...

    def tab_new(
        self, ctx: BrowserProviderContext, instance_id: str, url: str | None = None
    ) -> Mapping[str, Any]: ...

    def tab_list(
        self, ctx: BrowserProviderContext, instance_id: str | None = None
    ) -> list[Mapping[str, Any]] | Mapping[str, Any]: ...

    def tab_close(
        self, ctx: BrowserProviderContext, tab_id: str
    ) -> Mapping[str, Any] | None: ...

    def tab_navigate(
        self,
        ctx: BrowserProviderContext,
        tab_id: str,
        url: str,
        options: NavigateOptions | None = None,
    ) -> Mapping[str, Any]: ...

    def tab_snapshot(
        self,
        ctx: BrowserProviderContext,
        tab_id: str,
        options: SnapshotOptions | None = None,
    ) -> Mapping[str, Any]: ...

    def tab_text(
        self,
        ctx: BrowserProviderContext,
        tab_id: str,
        options: TextOptions | None = None,
    ) -> Mapping[str, Any]: ...

    def tab_screenshot(
        self,
        ctx: BrowserProviderContext,
        tab_id: str,
        options: OutputOptions | None = None,
    ) -> Mapping[str, Any]: ...

    def tab_pdf(
        self,
        ctx: BrowserProviderContext,
        tab_id: str,
        options: OutputOptions | None = None,
    ) -> Mapping[str, Any]: ...

    def tab_action(
        self, ctx: BrowserProviderContext, tab_id: str, action: BrowserAction
    ) -> Mapping[str, Any]: ...

    def tab_actions(
        self, ctx: BrowserProviderContext, tab_id: str, actions: list[BrowserAction]
    ) -> Mapping[str, Any]: ...

    def tab_lock(
        self,
        ctx: BrowserProviderContext,
        tab_id: str,
        owner: str | None = None,
        ttl_s: int | None = None,
    ) -> Mapping[str, Any]: ...

    def tab_unlock(
        self, ctx: BrowserProviderContext, tab_id: str, owner: str | None = None
    ) -> Mapping[str, Any] | None: ...


ProviderRegisterFn = Callable[["BrowserProviderRegistry"], None]
_LOG = logging.getLogger(__name__)


def _iter_entry_points(group: str) -> list[EntryPoint]:
    try:
        eps = entry_points(group=group)
        return sorted(eps, key=lambda ep: ep.name)
    except TypeError:
        all_eps = entry_points()
        fallback_eps = all_eps.get(group, [])
        return sorted(fallback_eps, key=lambda ep: ep.name)


class BrowserProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, BrowserProvider] = {}
        self._loaded_entry_points: set[str] = set()

    def register(self, provider: BrowserProvider) -> None:
        provider_id = str(getattr(provider, "provider_id", "")).strip()
        if not provider_id:
            raise ValueError("provider_id is required")
        if provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> BrowserProvider:
        key = str(provider_id).strip()
        if key not in self._providers:
            raise KeyError(key)
        return self._providers[key]

    def items(self) -> list[tuple[str, BrowserProvider]]:
        return sorted(self._providers.items(), key=lambda row: row[0])

    def list_provider_ids(self) -> list[str]:
        return sorted(self._providers)

    def provider_ids_with_capability(self, capability: str) -> list[str]:
        required = str(capability or "").strip()
        if not required:
            return []
        out: list[str] = []
        for provider_id, provider in self.items():
            if bool(getattr(provider.capabilities, required, False)):
                out.append(provider_id)
        return out

    def load_entry_points(
        self, *, group: str = "openminion.browser_providers"
    ) -> list[str]:
        loaded: list[str] = []
        for ep in _iter_entry_points(group):
            cache_key = f"{group}:{ep.name}:{ep.value}"
            if cache_key in self._loaded_entry_points:
                continue
            try:
                target = ep.load()
            except ModuleNotFoundError as exc:
                # Stale third-party entry points should not block the browser tool.
                _LOG.warning(
                    "skipping browser provider entry point name=%s target=%s reason=%s",
                    ep.name,
                    ep.value,
                    exc,
                )
                continue
            hook = _resolve_hook(target, hook_name="register_browser_provider")
            if hook is None:
                raise TypeError(
                    f"browser provider entry point '{ep.name}' must expose register_browser_provider(registry)"
                )
            hook(self)
            self._loaded_entry_points.add(cache_key)
            loaded.append(ep.name)
        return loaded


def provider_to_result(provider: BrowserProvider, *, op: str) -> BrowserResult:
    return BrowserResult(
        provider=provider.provider_id,
        capabilities=provider.capabilities,
        data={"op": op},
    )
