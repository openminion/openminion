"""Registry + capability checks for external durable-memory backends."""

from dataclasses import dataclass, field
from typing import Any, Callable

from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.backends.interfaces import (
    KnowledgeBackend,
    ensure_backend_compatibility,
)

ExternalBackendFactory = Callable[..., KnowledgeBackend]


@dataclass(frozen=True)
class ExternalBackendCapabilities:
    supports_relations: bool = True
    supports_candidate_workflow: bool = True
    supports_tier_history: bool = True
    supports_portability: bool = True
    supports_semantic_search: bool = False


@dataclass(frozen=True)
class ExternalBackendRegistration:
    name: str
    factory: ExternalBackendFactory
    capabilities: ExternalBackendCapabilities = field(
        default_factory=ExternalBackendCapabilities
    )


@dataclass(frozen=True)
class ExternalBackendCapabilityReport:
    adapter: str
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_required


_EXTERNAL_BACKENDS: dict[str, ExternalBackendRegistration] = {}
_REQUIRED_CAPABILITIES = (
    "supports_relations",
    "supports_candidate_workflow",
    "supports_tier_history",
    "supports_portability",
)
_OPTIONAL_CAPABILITIES = ("supports_semantic_search",)


def register_external_backend(
    name: str,
    *,
    factory: ExternalBackendFactory,
    capabilities: ExternalBackendCapabilities | None = None,
) -> None:
    normalized = str(name or "").strip().lower()
    if not normalized:
        raise InvalidArgumentError("external backend name is required")
    _EXTERNAL_BACKENDS[normalized] = ExternalBackendRegistration(
        name=normalized,
        factory=factory,
        capabilities=capabilities or ExternalBackendCapabilities(),
    )


def list_registered_external_backends() -> tuple[str, ...]:
    return tuple(sorted(_EXTERNAL_BACKENDS))


def get_registered_external_backend(name: str) -> ExternalBackendRegistration:
    normalized = str(name or "").strip().lower()
    try:
        return _EXTERNAL_BACKENDS[normalized]
    except KeyError as exc:
        known = ", ".join(sorted(_EXTERNAL_BACKENDS)) or "<none>"
        raise InvalidArgumentError(
            f"No external backend registered for {name!r}; known adapters: {known}.",
            details={"adapter": str(name or ""), "known_adapters": known},
        ) from exc


def validate_external_backend(
    *,
    adapter: str,
    backend: KnowledgeBackend,
    capabilities: ExternalBackendCapabilities,
    strict: bool = True,
) -> ExternalBackendCapabilityReport:
    ensure_backend_compatibility(backend, strict=True)
    missing_required = tuple(
        name
        for name in _REQUIRED_CAPABILITIES
        if not bool(getattr(capabilities, name, False))
    )
    missing_optional = tuple(
        name
        for name in _OPTIONAL_CAPABILITIES
        if not bool(getattr(capabilities, name, False))
    )
    report = ExternalBackendCapabilityReport(
        adapter=adapter,
        missing_required=missing_required,
        missing_optional=missing_optional,
    )
    if strict and report.missing_required:
        raise InvalidArgumentError(
            f"External backend {adapter!r} is missing required capabilities: "
            + ", ".join(report.missing_required),
            details={
                "adapter": adapter,
                "missing_required": list(report.missing_required),
            },
        )
    return report


def resolve_external_backend(*, adapter: str, strict: bool = True, **kwargs: Any):
    registration = get_registered_external_backend(adapter)
    backend = registration.factory(**kwargs)
    report = validate_external_backend(
        adapter=registration.name,
        backend=backend,
        capabilities=registration.capabilities,
        strict=strict,
    )
    return backend, report
