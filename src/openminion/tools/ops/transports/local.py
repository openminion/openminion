from pathlib import Path

from ..contracts import (
    OperationTarget,
    TransportFacts,
    TransportReadResult,
    TransportResult,
)
from ..interfaces import OutputSink
from .runtime import OUTPUT_LIMIT, ProcessTransport, platform_name


class LocalTransport(ProcessTransport):
    def connect(self, target: OperationTarget) -> TransportFacts:
        if target.kind != "local":
            raise ValueError("local transport requires a local target")
        return TransportFacts(
            kind="local",
            platform=platform_name(),
            connected=True,
            capabilities=target.capabilities,
        )

    def inspect(self, target: OperationTarget) -> TransportFacts:
        return self.connect(target)

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
        cwd: str = "",
    ) -> TransportResult:
        if target.kind != "local":
            raise ValueError("local transport requires a local target")
        return self._execute(
            argv,
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
            output_sink=output_sink,
            cwd=cwd,
        )

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult:
        del timeout_seconds
        self.connect(target)
        if max_bytes < 1 or max_bytes > OUTPUT_LIMIT:
            raise ValueError(f"max_bytes must be between 1 and {OUTPUT_LIMIT}")
        raw = Path(path).read_bytes()
        return TransportReadResult(
            path=path,
            content=raw[:max_bytes].decode(errors="replace"),
            truncated=len(raw) > max_bytes,
        )
