import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from .registry.catalog import ToolSpec


@dataclass
class ToolContext:
    """Execution context passed to plugins."""

    session_id: str | None
    trace_id: str | None
    agent_id: str | None
    workspace_root: str
    run_id: str
    policy_client: Any
    artifact_client: Any
    safety_client: Any
    env: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolPlan:
    summary: str
    requires_confirm: bool = False
    estimated_risk: str = "low"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None


@dataclass
class SafetyDecision:
    allowed: bool
    reason: str
    code: str = "OK"
    details: dict[str, Any] = field(default_factory=dict)


ToolConfirmationPreview = dict[str, Any]


@runtime_checkable
class SupportsConfirmationPreviewPayload(Protocol):
    def to_confirmation_dict(self) -> dict[str, Any]: ...


ConfirmationPreviewInput = ToolConfirmationPreview | SupportsConfirmationPreviewPayload


class ConfirmationPreviewError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def confirmation_preview_payload(
    preview: ConfirmationPreviewInput,
) -> dict[str, Any]:
    if isinstance(preview, Mapping):
        return dict(preview)
    return preview.to_confirmation_dict()


@dataclass(frozen=True)
class PolicyAuthorizationDescriptor:
    risk_class: str
    side_effects: str
    reversibility: str
    confirmation_ttl_seconds: int
    required_subject_id: str | None = None
    requires_session: bool = False


POLICY_AUTHORIZATION_DESCRIPTORS = MappingProxyType(
    {
        ("blockchain", "send_transaction"): PolicyAuthorizationDescriptor(
            "financial", "external_account", "irreversible", 600
        ),
        ("commerce", "prepare_order"): PolicyAuthorizationDescriptor(
            "state_change", "external_account", "reversible", 600, "local", True
        ),
        ("commerce", "place_order"): PolicyAuthorizationDescriptor(
            "financial", "external_account", "irreversible", 600, "local", True
        ),
        ("commerce", "apply_order_action"): PolicyAuthorizationDescriptor(
            "financial", "external_account", "partially_reversible", 600, "local", True
        ),
    }
)
POLICY_AUTHORIZATION_PAIRS = frozenset(POLICY_AUTHORIZATION_DESCRIPTORS)


class ConfirmationPreviewBuilder(Protocol):
    def __call__(
        self,
        args: dict[str, Any],
        *,
        subject_id: str,
        session_id: str,
        tool_resources: Mapping[str, Any],
    ) -> ToolConfirmationPreview: ...


def is_policy_authorization_pair(tool: str, method: str) -> bool:
    return (tool, method) in POLICY_AUTHORIZATION_PAIRS


@dataclass(frozen=True)
class PolicyAuthorization:
    tool: str
    method: str
    invocation_hash: str
    approval_id: str
    grant_id: str
    duration_type: Literal["once"]
    subject_id: str = "local"
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not is_policy_authorization_pair(self.tool, self.method):
            raise ValueError(  # allow-bare-raise: immutable value contract validation
                "unsupported policy authorization tool/method pair"
            )
        if self.duration_type != "once":
            raise ValueError(  # allow-bare-raise: immutable value contract validation
                "policy authorization must be one-time"
            )
        descriptor = POLICY_AUTHORIZATION_DESCRIPTORS[(self.tool, self.method)]
        if (
            descriptor.required_subject_id is not None
            and self.subject_id != descriptor.required_subject_id
        ) or (descriptor.requires_session and not self.session_id):
            raise ValueError(  # allow-bare-raise: immutable value contract validation
                "authorization requires the trusted subject and session"
            )


PolicyAuthorizer = Callable[[dict[str, Any], Any, Any], PolicyAuthorization]


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    code: str = "OK"
    requires_confirm: bool = False
    modified_args: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    approval_id: str | None = None


def stable_invocation_hash(*, tool: str, method: str, args: dict[str, Any]) -> str:
    filtered_args = {
        key: value
        for key, value in (args or {}).items()
        if not str(key).startswith("_")
    }
    encoded = json.dumps(
        {"tool": str(tool), "method": str(method), "args": filtered_args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class ToolPlugin(Protocol):
    """Generic tool plugin contract for openminion-tool plugin packages."""

    tool_id: str
    capabilities: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    def invoke(self, ctx: ToolContext, input_data: dict[str, Any]) -> ToolResult: ...


@runtime_checkable
class SupportsDryRun(Protocol):
    def dry_run(self, input_data: dict[str, Any]) -> ToolPlan: ...


@runtime_checkable
class SupportsCancel(Protocol):
    def cancel(self, handle: str) -> bool: ...


@runtime_checkable
class SafetyAdapter(Protocol):
    def evaluate(self, *, tool: str, args: dict[str, Any]) -> SafetyDecision: ...


@runtime_checkable
class PolicyAdapter(Protocol):
    def evaluate(
        self, *, tool_name: str, tool_spec: "ToolSpec", args: dict[str, Any]
    ) -> PolicyDecision: ...
