import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from .registry.catalog import ToolSpec


BLOCKCHAIN_CONFIRMATION_PREVIEW_INVALID_MESSAGE = (
    "Blockchain transaction approval preview could not be verified."
)


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


@dataclass(frozen=True)
class BlockchainCallPreview:
    function_signature: str
    function_args: list[Any]


@dataclass(frozen=True)
class BlockchainSendConfirmationPreview:
    schema_version: Literal["blockchain-send-preview-v1"]
    chain_id: str
    from_address: str
    to_address: str
    value_wei: str
    transaction_type: str
    nonce: str
    gas_limit: str
    gas_price_wei: str | None
    max_fee_per_gas_wei: str | None
    max_priority_fee_per_gas_wei: str | None
    max_total_fee_wei: str
    calldata_bytes: str
    calldata_sha256: str
    calldata_hex: str | None
    preparation_digest: str
    call: BlockchainCallPreview | None
    opaque_calldata: bool


@dataclass(frozen=True)
class PolicyAuthorization:
    tool: Literal["blockchain"]
    method: Literal["send_transaction"]
    invocation_hash: str
    approval_id: str
    grant_id: str
    duration_type: Literal["once"]


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
