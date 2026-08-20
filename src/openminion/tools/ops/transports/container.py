from ..contracts import (
    OperationTarget,
    TransportFacts,
    TransportReadResult,
    TransportResult,
)
from ..interfaces import OutputSink
from .runtime import OUTPUT_LIMIT, ProcessTransport


class ContainerTransport(ProcessTransport):
    def __init__(self, runtime: str | None = None) -> None:
        super().__init__()
        if runtime is not None and runtime not in {"docker", "podman"}:
            raise ValueError("container runtime must be docker or podman")
        self.runtime = runtime

    def connect(self, target: OperationTarget) -> TransportFacts:
        if target.kind != "container":
            raise ValueError("container transport requires a container target")
        return TransportFacts(
            kind="container",
            platform=target.platform,
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
        if target.kind != "container":
            raise ValueError("container transport requires a container target")
        runtime = self.runtime or target.container_runtime
        selector = (
            ("--context", target.docker_context)
            if runtime == "docker" and target.docker_context
            else (
                ("--connection", target.podman_connection)
                if runtime == "podman" and target.podman_connection
                else ()
            )
        )
        runtime_argv = (
            (runtime, *selector, "exec", "-w", cwd, target.container, *argv)
            if cwd
            else (runtime, *selector, "exec", target.container, *argv)
        )
        return self._execute(
            runtime_argv,
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
            output_sink=output_sink,
        )

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult:
        if max_bytes < 1 or max_bytes > OUTPUT_LIMIT:
            raise ValueError(f"max_bytes must be between 1 and {OUTPUT_LIMIT}")
        result = self.run(
            target,
            ("head", "-c", str(max_bytes + 1), "--", path),
            timeout_seconds=timeout_seconds,
        )
        if result.return_code != 0:
            raise OSError(result.stderr or f"unable to read {path}")
        return TransportReadResult(
            path=path,
            content=result.stdout[:max_bytes],
            truncated=len(result.stdout) > max_bytes,
        )
