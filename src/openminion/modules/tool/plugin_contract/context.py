# ruff: noqa: F403,F405
from .common import *
from .invocation import ArtifactRef, ToolInvocation
from .schemas import ToolCapabilities


@dataclass
class PolicyDecision:
    action: PolicyAction
    reason: str = ""
    code: str = "POLICY_DENIED"
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ArtifactSink(Protocol):
    def put_bytes(
        self,
        *,
        name: str,
        content: bytes,
        kind: str,
        meta: Optional[dict[str, Any]] = None,
    ) -> ArtifactRef: ...


@runtime_checkable
class EventSink(Protocol):
    def emit(self, *, event_name: str, payload: dict[str, Any]) -> None: ...


@runtime_checkable
class PolicyHook(Protocol):
    def check(
        self,
        *,
        invocation: ToolInvocation,
        ctx: "ToolContext",
        capabilities: ToolCapabilities,
    ) -> PolicyDecision: ...


@dataclass
class ToolContext:
    """Execution context passed to tool methods."""

    trace_id: str
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    working_dir: Optional[str] = None
    env: Optional[dict[str, str]] = None
    artifact_sink: Optional[ArtifactSink] = None
    event_sink: Optional[EventSink] = None
    logger: Any = None
    runtime: Optional["ToolRuntime"] = None
    extras: dict[str, Any] = field(default_factory=dict)

    def resolved_logger(self) -> logging.Logger:
        if self.logger is not None:
            return self.logger
        return logging.getLogger("openminion.modules.tool.runtime.plugins")
