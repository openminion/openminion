import inspect
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from ..errors import LLMCtlError
from ..interfaces import ensure_llm_response_compatibility
from .contract import (
    Provider,
    ensure_provider,
    local_provider,
    stub_provider,
)
from .adapters import (
    anthropic_provider,
    cerebras_provider,
    claude_provider,
    cortensor_provider,
    echo_provider,
    groq_provider,
    ollama_provider,
    openai_provider,
    openrouter_provider,
)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def add(self, provider: Provider) -> None:
        name = str(getattr(provider, "name", "")).strip()
        if not name:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                "Provider must expose non-empty 'name'",
            )
        ensure_provider(provider, component_name=f"provider:{name}")
        ensure_llm_response_compatibility(provider, component_name=f"provider:{name}")
        if name in self._providers:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                f"Provider already registered: {name}",
                {"provider": name},
            )
        if not hasattr(provider, "complete"):
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                f"Provider '{name}' missing 'complete' method",
                {"provider": name},
            )
        self._providers[name] = provider

    def get(self, name: str) -> Provider:
        if name not in self._providers:
            raise KeyError(name)
        return self._providers[name]

    def list(self) -> dict[str, Provider]:
        return dict(self._providers)


def _provider_entry_points() -> list[EntryPoint]:
    try:
        eps = entry_points(group="llmctl.providers")
        return sorted(eps, key=lambda ep: ep.name)
    except TypeError:
        all_eps = entry_points()
        fallback_eps = all_eps.get("llmctl.providers", [])
        return sorted(fallback_eps, key=lambda ep: ep.name)


def register_builtin_providers(registry: ProviderRegistry) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    builtins = [
        stub_provider(),
        local_provider(),
        echo_provider(),
        openai_provider(),
        openrouter_provider(),
        anthropic_provider(),
        claude_provider(),
        ollama_provider(),
        groq_provider(),
        cerebras_provider(),
        cortensor_provider(),
    ]

    for provider in builtins:
        name = provider.name
        try:
            registry.add(provider)
            health = (
                provider.healthcheck({})
                if hasattr(provider, "healthcheck")
                else {"ok": True}
            )
            statuses.append(
                {
                    "name": name,
                    "source": "builtin",
                    "installed": True,
                    "loaded": True,
                    "healthy": bool(health.get("ok", True)),
                    "health": health,
                }
            )
        except Exception as exc:
            statuses.append(
                {
                    "name": name,
                    "source": "builtin",
                    "installed": True,
                    "loaded": False,
                    "healthy": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return statuses


def load_plugin_providers(registry: ProviderRegistry) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []

    for ep in _provider_entry_points():
        status: dict[str, Any] = {
            "name": ep.name,
            "module": ep.module,
            "source": "entry_point",
            "installed": True,
            "loaded": False,
            "healthy": None,
        }
        try:
            loaded = ep.load()
            provider: Provider | None
            if inspect.isclass(loaded):
                provider = loaded()
            elif callable(loaded) and not hasattr(loaded, "name"):
                provider = loaded()
            else:
                provider = loaded

            if provider is None:
                raise LLMCtlError(
                    "INVALID_ARGUMENT",
                    "Entry point did not return a provider object",
                    {"entry_point": ep.name},
                )

            registry.add(provider)
            status["loaded"] = True

            health = {"ok": True}
            if hasattr(provider, "healthcheck"):
                maybe_health = provider.healthcheck({})
                if isinstance(maybe_health, dict):
                    health = maybe_health
            status["healthy"] = bool(health.get("ok", True))
            status["health"] = health
        except Exception as exc:
            status["loaded"] = False
            status["healthy"] = False
            status["error"] = f"{type(exc).__name__}: {exc}"

        statuses.append(status)

    return statuses
