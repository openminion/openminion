from dataclasses import dataclass
from typing import Protocol


@dataclass
class SandboxExecResult:
    """Normalized exec result across all sandbox providers.

    ``exit_code`` of 0 signals success. ``stdout`` and ``stderr`` carry the
    captured streams; ``meta`` is free-form provider-specific metadata for
    callers that need to inspect resource usage, sandbox IDs, etc.
    """

    exit_code: int
    stdout: str
    stderr: str
    meta: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.meta is None:
            self.meta = {}


class SandboxAdapter(Protocol):
    """Sandboxadapter contract."""

    name: str

    def exec(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult: ...

    def close(self) -> None: ...
