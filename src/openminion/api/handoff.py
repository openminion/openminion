"""Handoff records and transfer tools for developer-facing agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec
from openminion.tools.decorator import _build_args_model

if TYPE_CHECKING:  # pragma: no cover
    from openminion.api.agent import Agent


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
        result = handoff.target.run(message)
        return getattr(result, "text", str(result))

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
) -> "Agent[Any, Any]":
    """Construct a child agent that reuses the parent's runtime.

    Sharing the runtime keeps subagent spans nested under the parent's
    trace and lets memory/config flow naturally. The child does **not**
    own the runtime — closing it is a no-op on the parent's runtime.

    Parameters mirror :class:`Agent` for ergonomic parity.
    """

    from openminion.api.agent import Agent

    runtime = parent._ensure_runtime()  # noqa: SLF001 — same-package helper
    return Agent(
        instructions=instructions,
        output_type=output_type,
        runtime=runtime,
        model=model,
        tools=tools,
        name=name,
    )
