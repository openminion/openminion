import logging
from typing import Callable, Iterable

_STORAGE_LOGGER = logging.getLogger("openminion.storage")


def resolve_storage_provider(
    *,
    module: str,
    raw_provider: str | None,
    source_label: str,
    supported: Iterable[str] = ("sqlite",),
    default: str = "sqlite",
    path_mode: str | None = None,
    error_factory: Callable[[str], Exception] = ValueError,
    unsupported_message_builder: Callable[[str, tuple[str, ...], str], str]
    | None = None,
) -> str:
    """Normalize provider selection with consistent fail-closed logging."""
    allowed = tuple(
        token.strip().lower()
        for token in (str(item or "") for item in supported)
        if token.strip()
    )
    if not allowed:
        raise ValueError("supported providers must not be empty")

    provider = str(raw_provider or default).strip().lower()
    if provider not in allowed:
        _STORAGE_LOGGER.warning(
            "storage_provider_rejected module=%s provider=%s reason=unsupported",
            module,
            provider,
        )
        if unsupported_message_builder is None:
            supported_text = ", ".join(allowed)
            message = (
                f"Unsupported {source_label}={provider!r}. "
                f"Supported provider: {supported_text}."
            )
        else:
            message = unsupported_message_builder(provider, allowed, source_label)
        raise error_factory(message)

    if path_mode is None:
        _STORAGE_LOGGER.info(
            "storage_provider_selected module=%s provider=%s",
            module,
            provider,
        )
    else:
        _STORAGE_LOGGER.info(
            "storage_provider_selected module=%s provider=%s path_mode=%s",
            module,
            provider,
            path_mode,
        )
    return provider
