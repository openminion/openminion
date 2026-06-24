from dataclasses import dataclass

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_INTERNAL_MODE_ACT_CODING,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.loop.strategies.coding.contracts import (
    CODING_ALLOWED_TOOLS,
)


@dataclass(frozen=True, slots=True)
class LoopStrategy:
    name: str
    act_profile: str
    mode_name: str
    allowed_tools: frozenset[str]
    route_kind: str = "loop"
    checkpoint_support: bool = False


GENERAL_LOOP_STRATEGY = LoopStrategy(
    name="general",
    act_profile=BRAIN_ACT_PROFILE_GENERAL,
    mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    allowed_tools=frozenset(),
)

CODING_LOOP_STRATEGY = LoopStrategy(
    name="coding",
    act_profile=BRAIN_ACT_PROFILE_CODING,
    mode_name=BRAIN_INTERNAL_MODE_ACT_CODING,
    allowed_tools=CODING_ALLOWED_TOOLS,
    checkpoint_support=True,
)

RESEARCH_LOOP_STRATEGY = LoopStrategy(
    name="research",
    act_profile=BRAIN_ACT_PROFILE_RESEARCH,
    mode_name=BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    allowed_tools=frozenset(),
    checkpoint_support=True,
)

ORCHESTRATE_DISPATCH_STRATEGY = LoopStrategy(
    name="orchestrate",
    act_profile=BRAIN_ACT_PROFILE_ORCHESTRATE,
    mode_name=BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    allowed_tools=frozenset(),
    route_kind="delegation",
    checkpoint_support=True,
)

DELEGATED_TARGET_STRATEGY = LoopStrategy(
    name="delegated",
    act_profile=BRAIN_ACT_PROFILE_ORCHESTRATE,
    mode_name=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    allowed_tools=frozenset(),
    route_kind="delegation",
    checkpoint_support=True,
)


def resolve_loop_strategy(act_profile: str | None) -> LoopStrategy:
    normalized = str(act_profile or "").strip().lower()
    if normalized == BRAIN_ACT_PROFILE_CODING:
        return CODING_LOOP_STRATEGY
    if normalized == BRAIN_ACT_PROFILE_RESEARCH:
        return RESEARCH_LOOP_STRATEGY
    if normalized == BRAIN_ACT_PROFILE_ORCHESTRATE:
        return ORCHESTRATE_DISPATCH_STRATEGY
    return GENERAL_LOOP_STRATEGY


__all__ = [
    "CODING_LOOP_STRATEGY",
    "DELEGATED_TARGET_STRATEGY",
    "GENERAL_LOOP_STRATEGY",
    "LoopStrategy",
    "ORCHESTRATE_DISPATCH_STRATEGY",
    "RESEARCH_LOOP_STRATEGY",
    "resolve_loop_strategy",
]
