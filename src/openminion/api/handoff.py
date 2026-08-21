"""Handoff records and transfer tools for developer-facing agents."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec
from openminion.tools.decorator import _build_args_model

if TYPE_CHECKING:  # pragma: no cover
    from openminion.api.agent import Agent


@dataclass(frozen=True)
class SubagentRunContext:
    """Explicit bounded context for a developer-facing subagent run."""

    context_id: str
    parent_agent_id: str
    child_agent_id: str
    parent_run_id: str
    child_run_id: str
    trace_parent_id: str
    tool_allowlist: tuple[str, ...] = ()
    permission_scope: str = "inherit_bounded"
    repository_baseline: str = "shared"
    task_context: str = "isolated_child"
    memory_posture: str = "none"
    memory_grant_id: str | None = None
    memory_evidence_refs: tuple[str, ...] = ()
    deadline_iso: str = ""
    timeout_seconds: int | None = None
    cancel_policy: str = "cascade_from_parent"
    typed_result_handback: bool = True
    parent_transcript_inherited: bool = False
    hidden_reasoning_inherited: bool = False
    implicit_memory_write: bool = False
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.memory_posture not in {"none", "read_only_bounded"}:
            raise ValueError(f"unsupported memory_posture: {self.memory_posture!r}")
        if self.implicit_memory_write:
            raise ValueError("delegated memory writes are not supported in v1")

    def as_payload(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "parent_agent_id": self.parent_agent_id,
            "child_agent_id": self.child_agent_id,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "trace_parent_id": self.trace_parent_id,
            "tool_allowlist": list(self.tool_allowlist),
            "permission_scope": self.permission_scope,
            "repository_baseline": self.repository_baseline,
            "task_context": self.task_context,
            "memory_posture": self.memory_posture,
            "memory_grant_id": self.memory_grant_id,
            "memory_evidence_refs": list(self.memory_evidence_refs),
            "deadline_iso": self.deadline_iso,
            "timeout_seconds": self.timeout_seconds,
            "cancel_policy": self.cancel_policy,
            "typed_result_handback": self.typed_result_handback,
            "parent_transcript_inherited": self.parent_transcript_inherited,
            "hidden_reasoning_inherited": self.hidden_reasoning_inherited,
            "implicit_memory_write": self.implicit_memory_write,
            "cancelled": self.cancelled,
        }

    def as_inbound_metadata(self) -> dict[str, str]:
        return {
            "subagent_context_id": self.context_id,
            "subagent_parent_agent_id": self.parent_agent_id,
            "subagent_child_agent_id": self.child_agent_id,
            "subagent_parent_run_id": self.parent_run_id,
            "subagent_child_run_id": self.child_run_id,
            "subagent_trace_parent_id": self.trace_parent_id,
            "subagent_tool_allowlist": ",".join(self.tool_allowlist),
            "subagent_permission_scope": self.permission_scope,
            "subagent_repository_baseline": self.repository_baseline,
            "subagent_task_context": self.task_context,
            "subagent_memory_posture": self.memory_posture,
            "subagent_memory_grant_id": self.memory_grant_id or "",
            "subagent_memory_evidence_refs": ",".join(self.memory_evidence_refs),
            "subagent_deadline_iso": self.deadline_iso,
            "subagent_timeout_seconds": (
                "" if self.timeout_seconds is None else str(self.timeout_seconds)
            ),
            "subagent_cancel_policy": self.cancel_policy,
            "subagent_typed_result_handback": str(self.typed_result_handback).lower(),
            "subagent_parent_transcript_inherited": str(
                self.parent_transcript_inherited
            ).lower(),
            "subagent_hidden_reasoning_inherited": str(
                self.hidden_reasoning_inherited
            ).lower(),
            "subagent_implicit_memory_write": str(self.implicit_memory_write).lower(),
            "subagent_cancelled": str(self.cancelled).lower(),
        }


@dataclass
class Handoff:
    """A peer agent that the current agent can delegate to."""

    target: "Agent[Any, Any]"
    name: str | None = None
    description: str | None = None

    def resolved_name(self) -> str:
        if self.name:
            return self.name.strip()
        target_name = getattr(self.target, "name", None) or "agent"
        return f"transfer_to_{target_name}"

    def resolved_description(self) -> str:
        if self.description:
            return self.description.strip()
        instr = getattr(self.target, "instructions", None) or ""
        first_line = str(instr).strip().splitlines()[0] if instr else ""
        return first_line or "Delegate this turn to the peer agent."


def build_delegate_tool(handoff: Handoff) -> ToolDecl:
    """Compile a :class:`Handoff` into a ``transfer_to_<name>`` tool."""

    name = handoff.resolved_name()
    description = handoff.resolved_description()

    def _handler_fn(message: str) -> str:
        return cast(str, handoff.target.run(message).text)

    args_model = _build_args_model(_handler_fn, f"{name.replace('.', '_')}Args")

    def _handler(arguments: Any, _runtime_ctx: Any = None) -> Any:
        if hasattr(arguments, "model_dump"):
            payload = arguments.model_dump()
        else:
            payload = dict(arguments)
        return _handler_fn(**payload)

    return ToolDecl(
        name=name,
        args_model=args_model,
        handler=_handler,
        description=description,
        tags=("handoff",),
    )


def build_delegate_family_spec(handoffs: list[Handoff]) -> ToolFamilySpec | None:
    """Compile a list of handoffs into a single one-off tool family spec.

    Returns ``None`` when ``handoffs`` is empty so callers can skip
    registration entirely.
    """

    if not handoffs:
        return None
    decls = tuple(build_delegate_tool(h) for h in handoffs)
    return ToolFamilySpec(
        module_id="openminion.api.handoff.delegate",
        tools=decls,
        min_scope_default="WRITE_SAFE",
        common_tags=("handoff",),
    )


def subagent(
    parent: "Agent[Any, Any]",
    *,
    instructions: str | None = None,
    model: str | None = None,
    tools: list[str] | None = None,
    output_type: type | None = None,
    name: str | None = None,
    timeout_seconds: int | None = None,
    deadline_iso: str = "",
    memory_posture: str = "none",
    memory_grant_id: str | None = None,
    cancel_policy: str = "cascade_from_parent",
) -> "Agent[Any, Any]":
    """Construct a child agent with an explicit bounded run context.

    Sharing the runtime keeps execution on the parent's runtime owner, but the
    child carries explicit lineage, tool, memory, timeout, and result-handback
    metadata. The child does **not** own the runtime — closing it is a no-op on
    the parent's runtime.

    Parameters mirror :class:`Agent` for ergonomic parity.
    """

    from openminion.api.agent import Agent

    runtime = parent._ensure_runtime()  # noqa: SLF001 — same-package helper
    parent_context = parent.subagent_context
    if (
        parent_context is not None
        and parent_context.memory_posture == "read_only_bounded"
        and memory_posture != "none"
    ):
        raise ValueError("delegated memory grants cannot be re-shared in v1")
    parent_id = str(getattr(parent, "name", "") or "parent").strip() or "parent"
    child_id = str(name or "subagent").strip() or "subagent"
    context_id = f"subagent-{uuid4().hex[:12]}"
    context = SubagentRunContext(
        context_id=context_id,
        parent_agent_id=parent_id,
        child_agent_id=child_id,
        parent_run_id=f"agent:{parent_id}",
        child_run_id=f"{context_id}:{child_id}",
        trace_parent_id=f"agent:{parent_id}",
        tool_allowlist=tuple(tools or ()),
        deadline_iso=str(deadline_iso or ""),
        timeout_seconds=timeout_seconds,
        memory_posture=str(memory_posture or "none"),
        memory_grant_id=memory_grant_id,
        cancel_policy=str(cancel_policy or "cascade_from_parent"),
    )
    return Agent(
        instructions=instructions,
        output_type=output_type,
        runtime=runtime,
        model=model,
        tools=tools,
        name=name,
        subagent_context=context,
    )
