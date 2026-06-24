"""PRV operator-tunable config."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParallelRolloutConfig:
    """Operator-tunable parallel-rollout config."""

    enabled: bool = False
    n_rollouts: int = 3
    max_parallelism: int = 3
    eligible_step_kinds: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: int = 30

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.n_rollouts < 1:
            raise ValueError("n_rollouts must be >= 1")
        if self.max_parallelism < 1:
            raise ValueError("max_parallelism must be >= 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
