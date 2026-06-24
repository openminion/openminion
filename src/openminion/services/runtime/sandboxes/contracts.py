from dataclasses import dataclass
from typing import Optional, Protocol


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
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> SandboxExecResult: ...

    def close(self) -> None: ...
