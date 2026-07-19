"""Explicit provider capability facts used before provider calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .schemas import (
    LLMCatalogConfig,
    ProviderCapabilityName,
    ProviderProfile,
    RuntimeLLMRequest,
)

_CAPABILITY_ATTRIBUTES: dict[ProviderCapabilityName, str] = {
    "json": "supports_json",
    "tools": "supports_tools",
    "vision": "supports_vision",
    "streaming": "supports_streaming",
    "prompt_caching": "supports_prompt_caching",
}


@dataclass(frozen=True)
class ProviderCapabilityRow:
    """One reproducible provider-profile capability matrix row."""

    profile_id: str
    provider: str
    model: str
    json: bool
    tools: bool
    vision: bool
    streaming: bool
    prompt_caching: bool
    cost: bool
    auth: bool

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def profile_capability_facts(
    profile: ProviderProfile,
) -> dict[ProviderCapabilityName, bool]:
    facts: dict[ProviderCapabilityName, bool] = {
        name: bool(getattr(profile, attribute))
        for name, attribute in _CAPABILITY_ATTRIBUTES.items()
    }
    facts["cost"] = bool(
        profile.cost_hint
        and (
            profile.cost_hint.input_per_1k is not None
            or profile.cost_hint.output_per_1k is not None
        )
    )
    facts["auth"] = bool(str(profile.auth_ref or "").strip())
    return facts


def request_capability_requirements(
    request: RuntimeLLMRequest,
) -> tuple[ProviderCapabilityName, ...]:
    required: list[ProviderCapabilityName] = []
    for capability in request.required_capabilities:
        if capability not in required:
            required.append(capability)
    if request.output_schema is not None and "json" not in required:
        required.append("json")
    return tuple(required)


def missing_profile_capabilities(
    profile: ProviderProfile,
    required: Iterable[ProviderCapabilityName],
) -> tuple[ProviderCapabilityName, ...]:
    facts = profile_capability_facts(profile)
    return tuple(capability for capability in required if not facts[capability])


def capability_error_details(
    profile: ProviderProfile,
    request: RuntimeLLMRequest,
) -> dict[str, object] | None:
    required = request_capability_requirements(request)
    missing = missing_profile_capabilities(profile, required)
    if not missing:
        return None
    return {
        "profile_id": profile.id,
        "required_capabilities": list(required),
        "missing_capabilities": list(missing),
        "capability_facts": profile_capability_facts(profile),
    }


def provider_capability_matrix(
    catalog: LLMCatalogConfig,
) -> tuple[ProviderCapabilityRow, ...]:
    rows: list[ProviderCapabilityRow] = []
    for profile in catalog.profiles:
        facts = profile_capability_facts(profile)
        rows.append(
            ProviderCapabilityRow(
                profile_id=profile.id,
                provider=profile.provider,
                model=profile.model,
                **facts,
            )
        )
    return tuple(rows)


__all__ = [
    "ProviderCapabilityRow",
    "capability_error_details",
    "missing_profile_capabilities",
    "profile_capability_facts",
    "provider_capability_matrix",
    "request_capability_requirements",
]
