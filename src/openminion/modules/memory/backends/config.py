"""Config helpers for durable-memory backend selection below the MDCG seam."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from openminion.modules.memory.errors import InvalidArgumentError

DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER = "sophiagraph"
_SUPPORTED_BACKEND_PROVIDERS = frozenset({"sophiagraph", "none", "external"})


@dataclass(frozen=True)
class KnowledgeBackendConfig:
    """Normalized lower-level backend selection under ``runtime.memory_provider``."""

    provider: str = DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER
    external_adapter: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def _read_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    result: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        attr = getattr(value, name, None)
        if callable(attr):
            continue
        result[name] = attr
    return result


def _normalize_provider(raw: Any) -> str:
    provider = str(raw or DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER).strip().lower()
    if provider not in _SUPPORTED_BACKEND_PROVIDERS:
        supported = ", ".join(sorted(_SUPPORTED_BACKEND_PROVIDERS))
        raise InvalidArgumentError(
            "Unsupported memory.backend.provider="
            f"{raw!r}. Supported providers: {supported}."
        )
    return provider


def resolve_backend_config(config: Any | None) -> KnowledgeBackendConfig:
    backend_source = (
        config.get("backend")
        if isinstance(config, dict)
        else getattr(config, "backend", None)
    )
    backend_cfg: Mapping[str, Any] = _read_mapping(backend_source)

    provider = _normalize_provider(backend_cfg.get("provider"))
    external_adapter = (
        str(backend_cfg.get("external_adapter", "") or "").strip() or None
    )
    options = _read_mapping(backend_cfg.get("options"))
    return KnowledgeBackendConfig(
        provider=provider,
        external_adapter=external_adapter,
        options=options,
    )


__all__ = [
    "DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER",
    "KnowledgeBackendConfig",
    "resolve_backend_config",
]
