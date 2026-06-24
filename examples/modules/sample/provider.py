from __future__ import annotations

from typing import Any, Callable

from openminion.modules.providers import (
    ModuleRegistry,
    ProviderNotFoundError,
    check_contract_version_compatibility,
)
from .config import SampleConfig
from .interfaces import SampleService
from .service import SampleServiceImpl


SampleProviderFactory = Callable[[dict[str, Any]], SampleService]


def create_sample_provider_registry() -> ModuleRegistry[SampleProviderFactory]:
    registry = ModuleRegistry[SampleProviderFactory](expected_contract_version="v1")

    registry.register(
        "default",
        lambda config: SampleServiceImpl(config=config),
        contract_version="v1",
    )

    registry.register(
        "uppercase",
        lambda config: SampleServiceImpl(
            config={**config, "prefix": "[", "suffix": "]"}
        ),
        contract_version="v1",
    )

    return registry


def get_sample_provider(
    registry: ModuleRegistry[SampleProviderFactory],
    provider_id: str,
    config: dict[str, Any] | None = None,
) -> SampleService:
    try:
        check_contract_version_compatibility(
            provided="v1",
            expected="v1",
            allow_higher=False,
        )
        return registry.get(provider_id)({} if config is None else config)
    except ProviderNotFoundError as exc:
        available = registry.list_providers()
        raise ProviderNotFoundError(
            f"Sample provider '{provider_id}' not found. "
            f"Available providers: {available if available else 'none'}"
        ) from exc


def validate_provider_config(config: SampleConfig) -> tuple[bool, str]:
    if not config.provider_id:
        return False, "provider_id is required"

    if not config.enabled:
        return False, "provider is disabled"

    return True, ""
