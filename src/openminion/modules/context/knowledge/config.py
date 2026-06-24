"""Operator-tunable config for the knowledge-graph layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .constants import (
    KNOWLEDGE_GRAPH_CAPABILITIES,
    KNOWLEDGE_GRAPHS_CONFIG_KEY,
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
)
from .errors import (
    InvalidCapabilityError,
    InvalidLayerError,
    MultiActiveSecondBrainError,
)
from .models import _validate_layer, _validate_tags

DEFAULT_RETRIEVAL_MAX_RESULTS = 12
DEFAULT_RETRIEVAL_MAX_CHARS = 4000
DEFAULT_REFRESH_MODE = "manual"
DEFAULT_GRAPHIFY_TIMEOUT_SECONDS = 30.0

_PROVIDER_CONFIG_FIELDS = frozenset(
    {
        "enabled",
        "optional_capabilities",
        "options",
        "provider",
        "refresh",
        "required_capabilities",
        "retrieval",
        "tags",
    }
)


def _validate_capability_list(values: Any, *, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise InvalidCapabilityError(
            f"{field_name} must be a sequence",
            details={"field": field_name, "value": values},
        )
    result: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if text not in KNOWLEDGE_GRAPH_CAPABILITIES:
            raise InvalidCapabilityError(
                f"Unknown capability {raw!r} in {field_name}",
                details={
                    "field": field_name,
                    "value": text,
                    "valid": sorted(KNOWLEDGE_GRAPH_CAPABILITIES),
                },
            )
        if text not in result:
            result.append(text)
    return tuple(result)


@dataclass(frozen=True)
class KnowledgeGraphRetrievalConfig:
    """Retrieval budgets for a knowledge-graph provider."""

    max_results: int = DEFAULT_RETRIEVAL_MAX_RESULTS
    max_chars: int = DEFAULT_RETRIEVAL_MAX_CHARS
    include_paths: bool = True
    include_explanations: bool = True


@dataclass(frozen=True)
class KnowledgeGraphRefreshConfig:
    """Refresh policy for a knowledge-graph provider."""

    mode: str = DEFAULT_REFRESH_MODE
    on_start: bool = False
    watch: bool = False


@dataclass(frozen=True)
class KnowledgeGraphProviderConfig:
    """Per-provider configuration block."""

    name: str
    provider: str
    enabled: bool = True
    tags: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    retrieval: KnowledgeGraphRetrievalConfig = field(
        default_factory=KnowledgeGraphRetrievalConfig
    )
    refresh: KnowledgeGraphRefreshConfig = field(
        default_factory=KnowledgeGraphRefreshConfig
    )
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "provider", str(self.provider or "").strip())
        object.__setattr__(self, "tags", _validate_tags(self.tags))
        object.__setattr__(
            self,
            "required_capabilities",
            _validate_capability_list(
                self.required_capabilities,
                field_name="required_capabilities",
            ),
        )
        object.__setattr__(
            self,
            "optional_capabilities",
            _validate_capability_list(
                self.optional_capabilities,
                field_name="optional_capabilities",
            ),
        )
        object.__setattr__(self, "options", dict(self.options or {}))


@dataclass(frozen=True)
class KnowledgeGraphLayerConfig:
    """Per-layer activation and provider catalog."""

    layer: str
    active: tuple[str, ...] = ()
    allow_multi_active: bool = False
    providers: Mapping[str, KnowledgeGraphProviderConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "layer", _validate_layer(self.layer))
        normalized_active = _normalize_active(self.active)
        if (
            self.layer == LAYER_SECOND_BRAIN
            and len(normalized_active) > 1
            and not self.allow_multi_active
        ):
            raise MultiActiveSecondBrainError(
                "second_brain layer accepts only one active provider unless "
                "allow_multi_active is explicitly enabled by a future migration",
                details={"active": list(normalized_active)},
            )
        object.__setattr__(self, "active", normalized_active)
        object.__setattr__(self, "providers", dict(self.providers or {}))


@dataclass(frozen=True)
class KnowledgeGraphsConfig:
    """Top-level knowledge-graph configuration block."""

    second_brain: KnowledgeGraphLayerConfig = field(
        default_factory=lambda: KnowledgeGraphLayerConfig(layer=LAYER_SECOND_BRAIN)
    )
    provider: KnowledgeGraphLayerConfig = field(
        default_factory=lambda: KnowledgeGraphLayerConfig(layer=LAYER_THIRD_BRAIN)
    )

    def __post_init__(self) -> None:
        if self.second_brain.layer != LAYER_SECOND_BRAIN:
            raise InvalidLayerError(
                "second_brain field must hold a second_brain layer config",
                details={"layer": self.second_brain.layer},
            )
        if self.provider.layer != LAYER_THIRD_BRAIN:
            raise InvalidLayerError(
                "provider field must hold a provider layer config",
                details={"layer": self.provider.layer},
            )


def resolve_knowledge_graphs_config(
    config_or_payload: Any | None,
) -> KnowledgeGraphsConfig:
    """Resolve the typed knowledge-graph config from OpenMinion config data."""
    if config_or_payload is None:
        return KnowledgeGraphsConfig()
    payload = _extract_payload(config_or_payload)
    if not payload:
        return KnowledgeGraphsConfig()
    return knowledge_graphs_config_from_mapping(payload)


def knowledge_graphs_config_from_mapping(
    payload: Mapping[str, Any],
) -> KnowledgeGraphsConfig:
    """Parse a raw ``knowledge_graphs`` mapping into typed config DTOs."""
    root = _require_mapping(payload, field_name=KNOWLEDGE_GRAPHS_CONFIG_KEY)
    return KnowledgeGraphsConfig(
        second_brain=_layer_config_from_mapping(
            root.get(LAYER_SECOND_BRAIN),
            layer=LAYER_SECOND_BRAIN,
        ),
        provider=_layer_config_from_mapping(
            root.get(LAYER_THIRD_BRAIN),
            layer=LAYER_THIRD_BRAIN,
        ),
    )


def _normalize_active(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (bytes, bytearray)):
        return ()
    result: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _extract_payload(config_or_payload: Any) -> Mapping[str, Any]:
    if isinstance(config_or_payload, Mapping):
        if KNOWLEDGE_GRAPHS_CONFIG_KEY in config_or_payload:
            return _require_mapping(
                config_or_payload.get(KNOWLEDGE_GRAPHS_CONFIG_KEY),
                field_name=KNOWLEDGE_GRAPHS_CONFIG_KEY,
            )
        return _require_mapping(
            config_or_payload, field_name=KNOWLEDGE_GRAPHS_CONFIG_KEY
        )
    module_configs = getattr(config_or_payload, "module_configs", {}) or {}
    if not isinstance(module_configs, Mapping):
        return {}
    value = module_configs.get(KNOWLEDGE_GRAPHS_CONFIG_KEY)
    if value is None:
        return {}
    return _require_mapping(value, field_name=KNOWLEDGE_GRAPHS_CONFIG_KEY)


def _layer_config_from_mapping(
    payload: Any,
    *,
    layer: str,
) -> KnowledgeGraphLayerConfig:
    raw = _optional_mapping(
        payload, field_name=f"{KNOWLEDGE_GRAPHS_CONFIG_KEY}.{layer}"
    )
    providers_payload = _optional_mapping(
        raw.get("providers"),
        field_name=f"{KNOWLEDGE_GRAPHS_CONFIG_KEY}.{layer}.providers",
    )
    providers = {
        str(name).strip(): _provider_config_from_mapping(str(name).strip(), value)
        for name, value in providers_payload.items()
        if str(name).strip()
    }
    return KnowledgeGraphLayerConfig(
        layer=layer,
        active=_normalize_active(raw.get("active")),
        allow_multi_active=bool(raw.get("allow_multi_active", False)),
        providers=providers,
    )


def _provider_config_from_mapping(
    name: str,
    payload: Any,
) -> KnowledgeGraphProviderConfig:
    raw = _require_mapping(payload, field_name=f"provider {name!r}")
    explicit_options = _optional_mapping(
        raw.get("options"),
        field_name=f"provider {name!r}.options",
    )
    passthrough_options = {
        str(key): value
        for key, value in raw.items()
        if str(key) not in _PROVIDER_CONFIG_FIELDS
    }
    options = {**passthrough_options, **dict(explicit_options)}
    return KnowledgeGraphProviderConfig(
        name=name,
        provider=str(raw.get("provider") or name).strip(),
        enabled=bool(raw.get("enabled", True)),
        tags=_sequence_value(raw.get("tags")),
        required_capabilities=_sequence_value(raw.get("required_capabilities")),
        optional_capabilities=_sequence_value(raw.get("optional_capabilities")),
        retrieval=_retrieval_config_from_mapping(raw.get("retrieval")),
        refresh=_refresh_config_from_mapping(raw.get("refresh")),
        options=options,
    )


def _retrieval_config_from_mapping(payload: Any) -> KnowledgeGraphRetrievalConfig:
    raw = _optional_mapping(payload, field_name="retrieval")
    return KnowledgeGraphRetrievalConfig(
        max_results=_positive_int(
            raw.get("max_results"),
            default=DEFAULT_RETRIEVAL_MAX_RESULTS,
            field_name="retrieval.max_results",
        ),
        max_chars=_positive_int(
            raw.get("max_chars"),
            default=DEFAULT_RETRIEVAL_MAX_CHARS,
            field_name="retrieval.max_chars",
        ),
        include_paths=bool(raw.get("include_paths", True)),
        include_explanations=bool(raw.get("include_explanations", True)),
    )


def _refresh_config_from_mapping(payload: Any) -> KnowledgeGraphRefreshConfig:
    raw = _optional_mapping(payload, field_name="refresh")
    return KnowledgeGraphRefreshConfig(
        mode=str(raw.get("mode") or DEFAULT_REFRESH_MODE).strip()
        or DEFAULT_REFRESH_MODE,
        on_start=bool(raw.get("on_start", False)),
        watch=bool(raw.get("watch", False)),
    )


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidLayerError(
            f"{field_name} must be an object",
            details={"field": field_name, "value_type": type(value).__name__},
        )
    return value


def _optional_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _require_mapping(value, field_name=field_name)


def _sequence_value(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _positive_int(value: Any, *, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidCapabilityError(
            f"{field_name} must be an integer",
            details={"field": field_name, "value": value},
        ) from exc
    if parsed < 1:
        raise InvalidCapabilityError(
            f"{field_name} must be >= 1",
            details={"field": field_name, "value": parsed},
        )
    return parsed
