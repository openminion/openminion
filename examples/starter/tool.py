from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass
class ToolContext:
    run_id: str
    session_id: str
    allowed_scopes: Tuple[str, ...] = ()
    budget_steps_remaining: int = 10


@dataclass
class ToolRequest:
    arguments: Dict[str, str] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    content: str = ""
    error: str = ""


class HelloTool:
    """Copy-first tool template."""

    name = "hello_tool"
    required_scopes = ("tool.hello.read",)

    def execute(self, request: ToolRequest, context: ToolContext) -> ToolResult:
        granted = {scope.strip() for scope in context.allowed_scopes}
        missing = tuple(scope for scope in self.required_scopes if scope not in granted)
        if missing:
            return ToolResult(
                ok=False, error=f"missing required scopes: {', '.join(missing)}"
            )
        if context.budget_steps_remaining <= 0:
            return ToolResult(ok=False, error="tool budget exhausted")

        who = request.arguments.get("name", "world").strip() or "world"
        return ToolResult(ok=True, content=f"hello {who}")
