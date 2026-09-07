"""Handoff records and transfer tools for developer-facing agents."""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from typing import TYPE_CHECKING, Any
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
    memory_posture: str = "none"
    memory_grant_id: str | None = None
    timeout_seconds: int | None = None
    typed_result_handback: bool = True
    parent_transcript_inherited: bool = False
    hidden_reasoning_inherited: bool = False
    implicit_memory_write: bool = False
    cancelled: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "context_id",
            "parent_agent_id",
            "child_agent_id",
            "parent_run_id",
            "child_run_id",
            "trace_parent_id",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        if self.memory_posture not in {"none", "read_only_bounded"}:
            raise ValueError(f"unsupported memory_posture: {self.memory_posture!r}")
        if self.memory_posture == "read_only_bounded" and not self.memory_grant_id:
            raise ValueError("read_only_bounded requires memory_grant_id")
        if self.memory_posture == "none" and self.memory_grant_id:
            raise ValueError("memory_grant_id requires read_only_bounded")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if not self.typed_result_handback:
            raise ValueError("developer subagents require typed result handback")
        if self.parent_transcript_inherited or self.hidden_reasoning_inherited:
            raise ValueError("developer subagents require isolated context")
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
            "memory_posture": self.memory_posture,
            "memory_grant_id": self.memory_grant_id,
            "timeout_seconds": self.timeout_seconds,
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
            "subagent_memory_posture": self.memory_posture,
            "subagent_memory_grant_id": self.memory_grant_id or "",
            "subagent_timeout_seconds": (
                "" if self.timeout_seconds is None else str(self.timeout_seconds)
            ),
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

    def _handler_fn(message: str) -> dict[str, Any]:
        result = handoff.target.run(message)
        data = {"output": result.output}
        if result.run_id:
            data["run_id"] = result.run_id
        if result.run_state:
            data["run_state"] = result.run_state
        return {"ok": True, "content": result.text, "data": data}

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
) -> "Agent[Any, Any]":
    """Construct a child agent with an explicit bounded run context.

    Sharing the runtime keeps execution on the parent's runtime owner, but the
    child carries explicit lineage, tool, memory, timeout, and result-handback
    metadata. The child does **not** own the runtime — closing it is a no-op on
    the parent's runtime.

    Parameters mirror :class:`Agent` for ergonomic parity.
    """

    from openminion.api.agent import Agent

    parent_context = parent.subagent_context
    if (
        parent_context is not None
        and parent_context.memory_posture == "read_only_bounded"
        and memory_posture != "none"
    ):
        raise ValueError("delegated memory grants cannot be re-shared in v1")
    if memory_posture != "none" or memory_grant_id:
        raise ValueError("developer subagent memory grants are not bound in v1")
    runtime = parent._ensure_runtime()  # noqa: SLF001 — same-package helper
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
        timeout_seconds=_bounded_timeout_seconds(timeout_seconds, deadline_iso),
        memory_posture=str(memory_posture or "none"),
        memory_grant_id=memory_grant_id,
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


def _bounded_timeout_seconds(
    timeout_seconds: int | None,
    deadline_iso: str,
) -> int | None:
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    deadline = str(deadline_iso or "").strip()
    if not deadline:
        return timeout_seconds
    try:
        parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("deadline_iso must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("deadline_iso must include a timezone")
    remaining = ceil((parsed - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        raise ValueError("deadline_iso has elapsed")
    return remaining if timeout_seconds is None else min(timeout_seconds, remaining)
