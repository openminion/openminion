from typing import Any, Callable

from openminion.modules.providers import (
    ModuleRegistry,
    ProviderNotFoundError,
)


MemoryProviderFactory = Callable[[dict[str, Any]], Any]


def create_memory_provider_registry() -> ModuleRegistry[MemoryProviderFactory]:
    registry = ModuleRegistry[MemoryProviderFactory](expected_contract_version="v1")
    from openminion.modules.memory.memory_v2_hello_world import HelloWorldMemoryProvider

    registry.register(
        "hello_world",
        lambda config: HelloWorldMemoryProvider(),
        contract_version="v1",
    )

    return registry


def get_memory_provider(
    registry: ModuleRegistry[MemoryProviderFactory],
    provider_id: str,
    config: dict[str, Any] | None = None,
) -> Any:
    config = config or {}
    try:
        factory = registry.get(provider_id)
        return factory(config)
    except ProviderNotFoundError as exc:
        available = registry.list_providers()
        raise ProviderNotFoundError(
            f"Memory provider '{provider_id}' not found. "
            f"Available: {available if available else 'none'}"
        ) from exc
