"""Typed rollout plan and result contracts."""

from dataclasses import dataclass, field
from typing import Any, Literal


IsolationKind = Literal["worktree", "tempdir", "in_process"]


@dataclass(frozen=True)
class RolloutPlan:
    """Typed plan for one parallel-rollout step."""

    step_id: str
    n_rollouts: int
    max_parallelism: int
    isolation_kind: IsolationKind
    scorer_id: str
    timeout_seconds: int

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.n_rollouts < 1:
            raise ValueError("n_rollouts must be >= 1")
        if self.max_parallelism < 1:
            raise ValueError("max_parallelism must be >= 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")


@dataclass(frozen=True)
class RolloutResult:
    """Typed per-rollout result."""

    rollout_id: str
    output: Any
    quality_score: float = 0.0
    latency_ms: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return not bool(self.error)
