from __future__ import annotations

from collections.abc import Iterable

from .evaluation import CapabilityScenario, CapabilityScenarioResult, evaluate_scenario
from .registry import CapabilityPackRegistry
from .resolver import activate_pack
from .schemas import ActiveCapabilityPack, CapabilityPackManifest


def list_packs(registry: CapabilityPackRegistry) -> tuple[CapabilityPackManifest, ...]:
    return registry.list()


def inspect_pack(
    registry: CapabilityPackRegistry, pack_id: str
) -> CapabilityPackManifest:
    return registry.get(pack_id)


def activate_registered_pack(
    registry: CapabilityPackRegistry,
    *,
    pack_id: str,
    session_id: str,
    available_tools: Iterable[str],
    available_skills: Iterable[str],
) -> ActiveCapabilityPack:
    return activate_pack(
        registry.get(pack_id),
        session_id=session_id,
        available_tools=available_tools,
        available_skills=available_skills,
    )


def smoke_pack(
    manifest: CapabilityPackManifest,
    scenarios: Iterable[CapabilityScenario],
) -> tuple[CapabilityScenarioResult, ...]:
    return tuple(
        evaluate_scenario(manifest.policy_profile, scenario) for scenario in scenarios
    )
