import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.contracts.models import BrainClient
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)


class OpenMinionIntegrationError(RuntimeError):
    """Raised when OpenMinion integration cannot be initialized."""


_TURN_FAILURE_TEXT_MAP: tuple[tuple[str, str], ...] = (
    (
        "finalization_status contract",
        "The model ended the turn without the required completion contract. "
        "Please try again.",
    ),
    (
        "required completion contract",
        "The model ended the turn without the required completion contract. "
        "Please try again.",
    ),
    (
        "adaptive loop stopped unexpectedly",
        "The turn stopped unexpectedly before it could complete. Please try again.",
    ),
)


def _user_facing_turn_failure(text: str, metadata: dict[str, Any]) -> str | None:
    candidates = [text]
    for key in ("error", "error_message", "tool_loop_termination_reason"):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            candidates.append(value)

    lowered_candidates = [candidate.lower() for candidate in candidates if candidate]
    for marker, rendered in _TURN_FAILURE_TEXT_MAP:
        if any(marker in candidate for candidate in lowered_candidates):
            return rendered
    return None


@dataclass
class OpenMinionBrainClient(BrainClient):
    """Brain client backed by the OpenMinion runtime."""

    config_path: str | None = None
    home_root: str | None = None
    data_root: str | None = None
    channel: str = "console"
    target: str = "controlplane"
    deliver: bool = False
    runtime_factory: Callable[[str | None], Any] | None = None
    timeout_seconds: float = 600.0
    contract_version: str = field(default=CONTROLPLANE_INTERFACE_VERSION, init=False)

    def __post_init__(self) -> None:
        self._openminion_runtime = self._build_runtime()
        self._gateway = self._openminion_runtime.runtime_manager.gateway

    def _gateway_for_profile(self, profile_id: str) -> Any:
        resolver = getattr(self._openminion_runtime, "resolve_gateway", None)
        if callable(resolver):
            return resolver(profile_id)
        runtime_manager = getattr(self._openminion_runtime, "runtime_manager", None)
        manager_resolver = getattr(runtime_manager, "resolve_gateway", None)
        if callable(manager_resolver):
            return manager_resolver(profile_id)
        return self._gateway

    def _build_runtime(self) -> Any:
        if self.runtime_factory is None:
            raise OpenMinionIntegrationError(
                "OpenMinion runtime integration requires an explicit runtime_factory."
            )
        return self.runtime_factory(self.config_path)

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
        target = str(agent_id or self.target).strip() or self.target
        gateway = self._gateway_for_profile(target)
        inbound_metadata = {
            "controlplane.session_id": session_id,
            "controlplane.agent_id": agent_id,
            "controlplane.trace_id": trace_id,
            CALLER_HANDLES_DELIVERY_METADATA_KEY: "true",
            "turn_timeout_seconds": str(int(self.timeout_seconds)),
        }
        if attachment_refs:
            inbound_metadata["controlplane.attachment_refs"] = ",".join(attachment_refs)

        try:
            result = asyncio.run(
                asyncio.wait_for(
                    gateway.run_once(
                        channel=self.channel,
                        target=target,
                        message=message,
                        session_id=session_id,
                        idempotency_key=trace_id,
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
                "target": target,
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

        raw_text = str(getattr(result, "body", "") or "")
        text = _user_facing_turn_failure(raw_text, metadata) or raw_text

        return {
            "text": text,
            "session_id": metadata.get("session_id", session_id),
            "agent_id": agent_id,
            "trace_id": metadata.get("trace_id", trace_id),
            "channel": getattr(result, "channel", self.channel),
            "target": getattr(result, "target", target),
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
