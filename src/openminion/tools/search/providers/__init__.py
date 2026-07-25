"""Search provider contracts and registry public surface."""

from .registry import (
    SearchProvider,
    SearchProviderError,
    SearchProviderRegistry,
    _iter_entry_points,
    _is_provider,
    provider_registry,
    register_provider,
)

__all__ = [
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderRegistry",
    "_iter_entry_points",
    "_is_provider",
    "provider_registry",
    "register_provider",
]
