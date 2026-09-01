from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass(frozen=True, slots=True)
class RuntimeMemoryCloseResult:
    closed: bool
    reason_code: str


class RuntimeMemoryScheduler(Protocol):
    def bind_record_source(self, record_source: Any) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_alive(self) -> bool: ...


@dataclass
class RuntimeMemoryAssembly:
    """One shared memory runtime and its non-owning public views."""

    gateway: Any
    service: Any | None = None
    memctl: Any | None = None
    vector_adapter: Any | None = None
    scheduler: RuntimeMemoryScheduler | None = None
    _started: bool = field(default=False, init=False, repr=False)
    _close_result: RuntimeMemoryCloseResult | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def start(self) -> None:
        if self._started or self._close_result is not None:
            return
        if self.scheduler is not None:
            self.scheduler.bind_record_source(self.memctl)
            self.scheduler.start()
        self._started = True

    def close(self) -> RuntimeMemoryCloseResult:
        if self._close_result is not None:
            return self._close_result
        if self.scheduler is not None:
            self.scheduler.stop()
            if self.scheduler.is_alive():
                return RuntimeMemoryCloseResult(
                    closed=False,
                    reason_code="scheduler_still_running",
                )
        if self.service is not None:
            self.service.close()
        self._close_result = RuntimeMemoryCloseResult(
            closed=True,
            reason_code="closed",
        )
        return self._close_result

    def recover_pending_captures(
        self,
        *,
        sessions: Any,
        agent_id: str,
        extract_candidates: Callable[[Any, str, str, str], list[dict[str, Any]]],
        authorize: Callable[[str, str, str, str], bool],
        limit: int = 32,
    ) -> Any:
        from .capture_recovery import (
            CaptureRecoveryResult,
            recover_pending_capture_bundles,
        )

        if self.memctl is None:
            return CaptureRecoveryResult(scanned=0, recovered=0, pending=0)
        return recover_pending_capture_bundles(
            sessions=sessions,
            memctl=self.memctl,
            agent_id=agent_id,
            extract_candidates=extract_candidates,
            authorize=authorize,
            limit=limit,
        )


__all__ = [
    "RuntimeMemoryAssembly",
    "RuntimeMemoryCloseResult",
    "RuntimeMemoryScheduler",
]
