from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.loop.tools.iteration.helpers import (
    _tool_result_payload_from_action,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.llm.schemas import Message


@dataclass
class CodingLoopState:
    """Mutable state for one coding-mode invocation."""

    messages: list[Message] = field(default_factory=list)
    iteration: int = 0
    llm_calls: int = 0
    tool_calls_made: list[str] = field(default_factory=list)
    total_tool_calls: int = 0
    termination_reason: str = ""
    scratchpad: dict[str, Any] = field(default_factory=dict)
    seen_signatures: list[str] = field(default_factory=list)

    def append_tool_result(
        self,
        *,
        tool_name: str,
        action_result: ActionResult,
    ) -> None:
        results = [
            item
            for item in list(self.scratchpad.get("adaptive.tool_results", []) or [])
            if isinstance(item, dict)
        ]
        results.append(
            _tool_result_payload_from_action(
                tool_name=tool_name,
                action_result=action_result,
            )
        )
        self.scratchpad["adaptive.tool_results"] = results

    def telemetry_payload(self, allowed_tools: frozenset[str]) -> dict[str, Any]:
        parallel_calls = int(
            self.scratchpad.get(
                "coding.tool_calls_parallel",
                self.scratchpad.get("loop.tool_calls_parallel", 0),
            )
            or 0
        )
        sequential_calls = int(
            self.scratchpad.get(
                "coding.tool_calls_sequential",
                self.scratchpad.get("loop.tool_calls_sequential", 0),
            )
            or 0
        )
        payload = {
            "coding.loop_iterations": self.iteration,
            "coding.tool_calls": list(self.tool_calls_made),
            "coding.parallel_fan_out_count": int(
                self.scratchpad.get("coding.parallel_fan_out_count", parallel_calls)
                or 0
            ),
            "coding.tool_calls_sequential": sequential_calls,
            "coding.tool_calls_parallel": parallel_calls,
            "coding.plan_phases_executed": list(
                self.scratchpad.get("coding.plan_phases_executed", []) or []
            ),
            "coding.current_phase": str(
                self.scratchpad.get("coding.current_phase", "") or ""
            ),
            "coding.open_issues_count": int(
                self.scratchpad.get("coding.open_issues_count", 0) or 0
            ),
            "coding.self_corrections": int(
                self.scratchpad.get("coding.self_corrections", 0) or 0
            ),
            "coding.verify_gate_blocks": int(
                self.scratchpad.get("coding.verify_gate_blocks", 0) or 0
            ),
            "coding.autonomous_iterations": int(
                self.scratchpad.get("coding.autonomous_iterations", 0) or 0
            ),
            "coding.llm_calls": self.llm_calls,
            "coding.termination_reason": self.termination_reason,
            "coding.allowed_tools": sorted(allowed_tools),
        }
        tool_results = [
            item
            for item in list(self.scratchpad.get("adaptive.tool_results", []) or [])
            if isinstance(item, dict)
        ]
        if tool_results:
            payload["tool_results"] = tool_results
            payload["tool_calls_count"] = len(tool_results)
            payload["tool_execution_count"] = len(tool_results)
            payload["tool_verified"] = all(
                bool(item.get("verified")) for item in tool_results
            )
        for key in (
            "coding.verifier_goal_id",
            "coding.verifier_verdict",
            "coding.verifier_result_count",
            "coding.verify_gate_reason",
            "coding.verifier_unbound_count",
        ):
            if key in self.scratchpad:
                payload[key] = self.scratchpad[key]
        return payload
