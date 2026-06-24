"""RMP operator-tunable config."""

from dataclasses import dataclass, field

from openminion.modules.context.repo_map.constants import (
    RMP_DEFAULT_PROFILE_GATE,
    RMP_DEFAULT_TOKEN_BUDGET,
)


@dataclass(frozen=True)
class RepoMapConfig:
    """Operator-tunable repo-map config — default disabled."""

    enabled: bool = False
    token_budget: int = RMP_DEFAULT_TOKEN_BUDGET
    profile_gate: tuple[str, ...] = field(
        default_factory=lambda: tuple(RMP_DEFAULT_PROFILE_GATE)
    )


__all__ = ["RepoMapConfig"]
