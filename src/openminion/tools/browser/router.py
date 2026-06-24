from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

from .providers import BrowserProvider, BrowserProviderRegistry


LookupAffinity = Callable[[str], str | None]
RememberAffinity = Callable[[str, str], None]


@dataclass(frozen=True)
class BrowserRoutingConfig:
    default_provider: str = ""
    provider_order: tuple[str, ...] = ()
    lookup_instance_affinity: LookupAffinity | None = None
    lookup_tab_affinity: LookupAffinity | None = None
    remember_instance_affinity: RememberAffinity | None = None
    remember_tab_affinity: RememberAffinity | None = None


class BrowserRouter:
    def __init__(
        self,
        registry: BrowserProviderRegistry,
        *,
        config: BrowserRoutingConfig | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or BrowserRoutingConfig()
        self._instance_affinity: dict[str, str] = {}
        self._tab_affinity: dict[str, str] = {}

    @property
    def default_provider(self) -> str:
        return str(self._config.default_provider or "").strip()

    def select_provider(
        self,
        *,
        requested_provider: str | None,
        agent_profile_provider: str | None,
        session_provider_override: str | None = None,
        instance_id: str | None = None,
        tab_id: str | None = None,
        runtime_default_provider: str | None = None,
        runtime_provider_order: Iterable[str] = (),
    ) -> BrowserProvider:
        provider_id = (
            self._normalize_provider_token(requested_provider)
            or self._normalize_provider_token(agent_profile_provider)
            or self._normalize_provider_token(session_provider_override)
            or self._provider_for_tab(tab_id)
            or self._provider_for_instance(instance_id)
        )
        if not provider_id:
            provider_id = self._resolve_implicit_provider_id(
                runtime_default_provider=runtime_default_provider,
                runtime_provider_order=runtime_provider_order,
            )
        if not provider_id:
            raise KeyError("no browser provider specified and no default configured")
        return self._registry.get(provider_id)

    @staticmethod
    def _normalize_provider_token(value: str | None) -> str:
        token = str(value or "").strip().lower()
        if token in {"", "auto"}:
            return ""
        return token

    def remember_affinity(
        self,
        *,
        provider_id: str,
        instance_id: str | None = None,
        tab_id: str | None = None,
    ) -> None:
        owner = str(provider_id or "").strip()
        if not owner:
            return
        if instance_id:
            key = str(instance_id).strip()
            if key:
                self._instance_affinity[key] = owner
                if self._config.remember_instance_affinity is not None:
                    self._config.remember_instance_affinity(key, owner)
        if tab_id:
            key = str(tab_id).strip()
            if key:
                self._tab_affinity[key] = owner
                if self._config.remember_tab_affinity is not None:
                    self._config.remember_tab_affinity(key, owner)

    def providers_with_capability(self, capability: str) -> list[str]:
        return self._registry.provider_ids_with_capability(capability)

    def _resolve_implicit_provider_id(
        self,
        *,
        runtime_default_provider: str | None,
        runtime_provider_order: Iterable[str],
    ) -> str:
        candidates = (
            [str(runtime_default_provider or "").strip()]
            + list(runtime_provider_order)
            + [self.default_provider]
            + list(self._config.provider_order)
        )
        provider_id = self._first_available_provider(candidates)
        if provider_id:
            return provider_id
        return self._auto_default_provider(preferred_order=runtime_provider_order)

    def _auto_default_provider(self, *, preferred_order: Iterable[str] = ()) -> str:
        provider_ids = self._registry.list_provider_ids()
        if not provider_ids:
            return ""
        preferred_provider = self._first_available_provider(
            list(preferred_order) + list(self._config.provider_order)
        )
        if preferred_provider:
            return preferred_provider
        for preferred in ("pinchtab", "playwright"):
            if preferred in provider_ids:
                return preferred
        return provider_ids[0]

    def _first_available_provider(self, candidates: Iterable[str]) -> str:
        available = set(self._registry.list_provider_ids())
        for candidate in candidates:
            token = str(candidate or "").strip()
            if token and token in available:
                return token
        return ""

    def _provider_for_instance(self, instance_id: str | None) -> str:
        key = str(instance_id or "").strip()
        if not key:
            return ""
        provider_id = self._instance_affinity.get(key, "")
        if provider_id:
            return provider_id
        if self._config.lookup_instance_affinity is None:
            return ""
        looked_up = str(self._config.lookup_instance_affinity(key) or "").strip()
        if looked_up:
            self._instance_affinity[key] = looked_up
        return looked_up

    def _provider_for_tab(self, tab_id: str | None) -> str:
        key = str(tab_id or "").strip()
        if not key:
            return ""
        provider_id = self._tab_affinity.get(key, "")
        if provider_id:
            return provider_id
        if self._config.lookup_tab_affinity is None:
            return ""
        looked_up = str(self._config.lookup_tab_affinity(key) or "").strip()
        if looked_up:
            self._tab_affinity[key] = looked_up
        return looked_up
