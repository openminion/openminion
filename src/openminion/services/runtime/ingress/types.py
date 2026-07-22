"""Typed request/result contracts for runtime ingress."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from collections.abc import Mapping

from openminion.base.config import RunProfileOverrides
from openminion.services.runtime.manager import TurnRequest
from openminion.services.runtime.manager import TurnHandle as ManagerTurnHandle
from openminion.modules.telemetry.usage import RunStats


class TurnRequestError(RuntimeError):
    """Raised when a turn request is invalid."""


class TurnTimeoutError(RuntimeError):
    """Raised when turn execution exceeds timeout."""


@dataclass(frozen=True)
class RuntimeTurnRequest:
    agent_id: str
    profile_agent_id: str
    message: str
    channel: str
    target: str
    timeout_seconds: float
    session_id: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    inbound_metadata: Mapping[str, str] | None = None
    deliver: bool = True
    forced_tools: tuple[str, ...] = ()
    capability_category: str | None = None
    run_profile_overrides: RunProfileOverrides = field(
        default_factory=RunProfileOverrides
    )


@dataclass(frozen=True)
class RuntimeTurnResult:
    id: str
    channel: str
    target: str
    body: str
    metadata: Mapping[str, Any]
    agent_id: str
    stats: RunStats | None = None

    def as_payload(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        payload: dict[str, Any] = {
            "id": self.id,
            "channel": self.channel,
            "target": self.target,
            "body": self.body,
            "metadata": metadata,
            "session_id": metadata.get("session_id", ""),
            "agent_id": self.agent_id,
        }
        if self.stats is not None and self.stats.has_any_data:
            payload["stats"] = self.stats.as_payload()
        run_id = str(metadata.get("run_id", "")).strip()
        if run_id:
            payload["run_id"] = run_id
        run_state = str(metadata.get("run_state", "")).strip()
        if run_state:
            payload["run_state"] = run_state
        return payload


@dataclass(frozen=True)
class RuntimeTurnHandle:
    request: TurnRequest
    handle: ManagerTurnHandle
    timeout_s: float

    @property
    def trace_id(self) -> str:
        return self.handle.trace_id

    def result(self, timeout_s: float | None = None) -> Any:
        effective_timeout = self.timeout_s if timeout_s is None else timeout_s
        return self.handle.result(timeout_s=effective_timeout)

    def stream(self, timeout_s: float | None = None) -> Any:
        return self.handle.stream(timeout_s=timeout_s)

    def cancel(self) -> bool:
        return bool(self.handle.cancel())


@dataclass(frozen=True)
class TurnContext:
    message: str
    forced_tools: tuple[str, ...]
    inbound_metadata: Mapping[str, str] | None


def freeze_metadata(metadata: dict[str, str] | None) -> Mapping[str, str] | None:
    if metadata is None:
        return None
    return MappingProxyType(dict(metadata))
