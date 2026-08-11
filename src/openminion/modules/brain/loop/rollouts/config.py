from dataclasses import dataclass


@dataclass(frozen=True)
class ParallelRolloutConfig:
    enabled: bool = False
    n_rollouts: int = 3
    max_parallelism: int = 3
    eligible_step_kinds: tuple[str, ...] = ()
    timeout_seconds: int = 30

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.n_rollouts < 1:
            raise ValueError("n_rollouts must be >= 1")
        if self.max_parallelism < 1:
            raise ValueError("max_parallelism must be >= 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
