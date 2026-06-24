import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.contracts.models import BrainClient


class OpenMinionIntegrationError(RuntimeError):
    """Raised when OpenMinion integration cannot be initialized."""


@dataclass
class OpenMinionBrainClient(BrainClient):
    """Brain client backed by the OpenMinion runtime."""

    config_path: str | None = None
    channel: str = "console"
    target: str = "controlplane"
    deliver: bool = False
    runtime_factory: Callable[[str | None], Any] | None = None
    timeout_seconds: float = 600.0
    contract_version: str = field(default=CONTROLPLANE_INTERFACE_VERSION, init=False)

    def __post_init__(self) -> None:
        self._openminion_runtime = self._build_runtime()
        self._gateway = self._openminion_runtime.runtime_manager.gateway

    def _build_runtime(self) -> Any:
        if self.runtime_factory is not None:
            return self.runtime_factory(self.config_path)

        try:
            from openminion.services.runtime import OpenMinionRuntime
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
            raise OpenMinionIntegrationError(
                "OpenMinion is not installed. Install the openminion package to enable integration."
            ) from exc

        return OpenMinionRuntime.from_config_path(self.config_path)

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict[str, Any]:
        message = user_text or ""
        inbound_metadata = {
            "controlplane.session_id": session_id,
            "controlplane.agent_id": agent_id,
            "controlplane.trace_id": trace_id,
            "turn_timeout_seconds": str(int(self.timeout_seconds)),
        }
        if attachment_refs:
            inbound_metadata["controlplane.attachment_refs"] = ",".join(attachment_refs)

        try:
            result = asyncio.run(
                asyncio.wait_for(
                    self._gateway.run_once(
                        channel=self.channel,
                        target=self.target,
                        message=message,
                        session_id=session_id,
                        request_id=trace_id,
                        inbound_metadata=inbound_metadata,
                        deliver=self.deliver,
                    ),
                    timeout=self.timeout_seconds,
                )
            )
        except asyncio.TimeoutError:
            # Return deterministic timeout error
            return {
                "text": "",
                "session_id": session_id,
                "agent_id": agent_id,
                "trace_id": trace_id,
                "channel": self.channel,
                "target": self.target,
                "metadata": {
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "error": "TURN_TIMEOUT",
                    "error_message": f"Turn execution timed out after {self.timeout_seconds}s",
                    "lifecycle_stage": "timeout",  # CRDU-03: Lifecycle stage marker
                    "retryable": True,
                },
            }

        metadata = dict(getattr(result, "metadata", {}) or {})
        metadata.setdefault("trace_id", trace_id)
        metadata.setdefault("session_id", session_id)

        if lifecycle_events := self._extract_lifecycle_events(result):
            metadata["lifecycle_events"] = lifecycle_events
            last_event = lifecycle_events[-1]
            metadata["lifecycle_stage"] = last_event.get("stage", "unknown")
            metadata["lifecycle_timestamp"] = last_event.get("timestamp")

        return {
            "text": getattr(result, "body", ""),
            "session_id": metadata.get("session_id", session_id),
            "agent_id": agent_id,
            "trace_id": metadata.get("trace_id", trace_id),
            "channel": getattr(result, "channel", self.channel),
            "target": getattr(result, "target", self.target),
            "metadata": metadata,
        }

    def _extract_lifecycle_events(self, result: Any) -> list[dict[str, Any]]:
        """Extract lifecycle events from gateway result metadata when present."""
        metadata = getattr(result, "metadata", {}) or {}

        if "lifecycle_events" in metadata:
            return metadata["lifecycle_events"]

        tool_results = metadata.get("tool_results", "")
        if isinstance(tool_results, str) and tool_results:
            try:
                import json

                parsed = json.loads(tool_results)
                if isinstance(parsed, list):
                    for item in parsed:
                        if (
                            isinstance(item, dict)
                            and item.get("tool_name") == "cortensor_complete"
                        ):
                            lifecycle_data = item.get("result", {}).get(
                                "lifecycle_events", []
                            )
                            if lifecycle_data:
                                return lifecycle_data
            except (json.JSONDecodeError, AttributeError):
                pass

        return []

    def close(self) -> None:
        closer = getattr(self._openminion_runtime, "close", None)
        if callable(closer):
            closer()
