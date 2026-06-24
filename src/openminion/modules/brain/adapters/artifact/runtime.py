import time
from typing import Any

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION


class ArtifactctlAdapter:
    """Adapter for artifact operations using openminion-artifact."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, artifactctl: ArtifactCtl) -> None:
        self.artifactctl = artifactctl

    @staticmethod
    def _metrics(start_time: float) -> dict[str, Any]:
        return {
            "latency_ms": int((time.monotonic() - start_time) * 1000),
            "tokens_used": 0,
            "cost_estimate": 0.0,
        }

    def _success_response(
        self,
        *,
        summary: str,
        outputs: dict[str, Any],
        start_time: float,
        artifact_refs: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "summary": summary,
            "outputs": outputs,
            "artifact_refs": artifact_refs or [],
            "memory_refs": [],
            "metrics": self._metrics(start_time),
        }

    def _error_response(
        self,
        *,
        summary: str,
        code: str,
        message: str,
        start_time: float,
    ) -> dict[str, Any]:
        return {
            "status": "error",
            "summary": summary,
            "outputs": {},
            "artifact_refs": [],
            "memory_refs": [],
            "metrics": self._metrics(start_time),
            "error": {"code": code, "message": message},
        }

    def _create_artifact(
        self,
        *,
        args: dict[str, Any],
        session_id: str,
        trace_id: str,
    ) -> Any:
        content = args.get("content")
        path = args.get("path")
        common_kwargs: dict[str, Any] = {
            "mime": args.get("mime"),
            "label": args.get("label"),
            "meta": args.get("meta"),
            "session_id": session_id,
            "trace_id": trace_id,
        }
        if content is not None:
            data = content.encode("utf-8") if isinstance(content, str) else content
            return self.artifactctl.ingest_bytes(data=data, **common_kwargs)
        if path is not None:
            return self.artifactctl.ingest_file(path=path, **common_kwargs)
        raise ValueError("Must provide either 'content' or 'path'")

    def _read_artifact(self, args: dict[str, Any]) -> tuple[str, Any]:
        ref_id = args.get("id")
        if not ref_id:
            raise ValueError("Must provide 'id'")
        view_type = args.get("view_type", "text")
        return str(ref_id), self.artifactctl.read_view(ref_id, view_type)

    def execute(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]:
        tool_name = str(command.get("tool_name", ""))
        args = command.get("args", {})
        start_time = time.monotonic()

        if tool_name == "create_artifact":
            try:
                ref = self._create_artifact(
                    args=args,
                    session_id=session_id,
                    trace_id=trace_id,
                )
                return self._success_response(
                    summary=f"Artifact created: {ref.ref}",
                    outputs={
                        "id": ref.ref,
                        "sha256": ref.sha256,
                        "size_bytes": ref.size_bytes,
                    },
                    artifact_refs=[{"ref": ref.ref, "role": "output"}],
                    start_time=start_time,
                )
            except Exception as exc:
                return self._error_response(
                    summary="Failed to create artifact",
                    code="ARTIFACT_ERROR",
                    message=str(exc),
                    start_time=start_time,
                )
        if tool_name == "read_artifact":
            try:
                ref_id, data = self._read_artifact(args)
                return self._success_response(
                    summary=f"Artifact read: {ref_id}",
                    outputs={"content": data},
                    start_time=start_time,
                )
            except Exception as exc:
                return self._error_response(
                    summary="Failed to read artifact",
                    code="ARTIFACT_ERROR",
                    message=str(exc),
                    start_time=start_time,
                )
        return self._error_response(
            summary=f"Unknown artifact tool: {tool_name}",
            code="NOT_FOUND",
            message=f"Tool '{tool_name}' not supported by ArtifactctlAdapter.",
            start_time=start_time,
        )
