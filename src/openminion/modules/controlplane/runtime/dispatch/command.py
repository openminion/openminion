from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ...contracts.models import CommandResult, ParsedCommand, ResolvedContext
from ..audit import emit_audit_event

JsonDict = dict[str, Any]


class CommandRegistry(Protocol):
    def execute(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult: ...


@dataclass
class CommandDispatcher:
    registry: CommandRegistry
    audit_logger: object | None = None

    def dispatch(self, command: ParsedCommand, ctx: ResolvedContext) -> JsonDict:
        self._audit(
            "cp.command.detected",
            canonical=command.canonical,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
        )
        result: CommandResult = self.registry.execute(command, ctx)
        event = "cp.command.executed" if result.ok else "cp.command.failed"
        self._audit(
            event,
            canonical=command.canonical,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
        )
        return {
            "type": "command_result",
            "ok": result.ok,
            "text": result.text,
            "data": result.data,
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
        }

    def _audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)
