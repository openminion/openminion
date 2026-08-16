from __future__ import annotations

from typing import Any, Mapping

from openminion.modules.tool import (
    Tool,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutionResult,
)


class HelloTool(Tool):
    name = "hello_tool"
    description = "Return a short greeting."
    policy = ToolExecutionPolicy(
        required_scopes_all=("tool.execute",),
        risk="low",
        budget_cost=1,
    )
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": [],
    }

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        del context
        who = str(arguments.get("name", "world")).strip() or "world"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=f"hello {who}",
            verified=True,
            data={"name": who},
        )
