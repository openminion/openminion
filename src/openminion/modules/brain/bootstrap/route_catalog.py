from dataclasses import dataclass
from typing import Any

from openminion.modules.brain.runtime.reasoning import ModeThinkingPolicy

from ..constants import BRAIN_DECISION_ROUTE_ACT, BRAIN_DECISION_ROUTE_RESPOND


@dataclass(frozen=True, slots=True)
class DecisionRouteDescriptor:
    name: str
    description: str
    category: str
    priority_hint: int
    thinking_policy: ModeThinkingPolicy | None = None
    decision_visible: bool = True
    registration_source: dict[str, Any] | None = None


_CORE_SOURCE = {
    "category": "essential_builtin",
    "module_name": "openminion.modules.brain.bootstrap.route_catalog",
    "label": "core",
    "optional": False,
}

_DESCRIPTORS: dict[str, DecisionRouteDescriptor] = {
    BRAIN_DECISION_ROUTE_RESPOND: DecisionRouteDescriptor(
        name=BRAIN_DECISION_ROUTE_RESPOND,
        description=(
            "answer directly with no tool or plan; use for greetings, chit-chat, or "
            "when execution adds no value. Do not use 'respond' for tool-eligible "
            "factual asks."
        ),
        category="response",
        priority_hint=10,
        thinking_policy=ModeThinkingPolicy(
            default_reasoning_profile="off",
            allowed_reasoning_profiles=("off", "minimal"),
            allow_request_override=True,
        ),
        registration_source=dict(_CORE_SOURCE),
    ),
    BRAIN_DECISION_ROUTE_ACT: DecisionRouteDescriptor(
        name=BRAIN_DECISION_ROUTE_ACT,
        description=(
            "execute work now through the shared act loop. Use act_profile for "
            "general, coding, or bounded research behavior. Use "
            "execution_target.kind='delegated' when another agent should execute "
            "the act task."
        ),
        category="action",
        priority_hint=20,
        thinking_policy=ModeThinkingPolicy(
            default_reasoning_profile="minimal",
            allowed_reasoning_profiles=("off", "minimal", "detailed"),
            allow_request_override=True,
        ),
        registration_source=dict(_CORE_SOURCE),
    ),
}


def is_route_enabled(profile: Any | None, mode_name: str) -> bool:
    if profile is None:
        return True
    mode_config = getattr(profile, "mode_config", None)
    if not isinstance(mode_config, dict):
        return True
    config = mode_config.get(str(mode_name or "").strip())
    return bool(getattr(config, "enabled", True))


def get_route_descriptor(mode_name: str) -> DecisionRouteDescriptor | None:
    normalized = str(mode_name or "").strip()
    if not normalized:
        return None
    return _DESCRIPTORS.get(normalized)


def registered_routes() -> list[str]:
    return sorted(_DESCRIPTORS)


def available_routes(profile: Any | None = None) -> list[str]:
    return [
        descriptor.name
        for descriptor in sorted(_DESCRIPTORS.values(), key=lambda item: item.name)
        if descriptor.decision_visible and is_route_enabled(profile, descriptor.name)
    ]


decision_visible_routes = available_routes


def decision_route_descriptions(profile: Any | None = None) -> dict[str, str]:
    ordered = sorted(
        (
            descriptor
            for descriptor in _DESCRIPTORS.values()
            if descriptor.decision_visible
            and is_route_enabled(profile, descriptor.name)
        ),
        key=lambda item: (item.priority_hint, item.name),
    )
    return {descriptor.name: descriptor.description for descriptor in ordered}


__all__ = [
    "DecisionRouteDescriptor",
    "available_routes",
    "decision_route_descriptions",
    "decision_visible_routes",
    "get_route_descriptor",
    "is_route_enabled",
    "registered_routes",
]
