"""Planning-flow helpers for the coding strategy handler."""

import json
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
    CODING_PUBLIC_TAG as _CODING_PUBLIC_TAG,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.tools import build_loop_thinking_metadata
from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.llm.schemas import Message
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_REPO_MAP,
)

from .llm import DefaultCodingLLMRuntime
from .plan import CodingPlan, coding_plan_from_payload


class CodingPlanningMixin:
    def _load_context_action_result(
        self: Any,
        ctx: ExecutionContext,
        *,
        title: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> Any | None:
        result = ctx.command_executor.execute_command(
            state=ctx.state,
            command=ToolCommand(title=title, tool_name=tool_name, args=args),
            logger=ctx.logger,
            include_reflect=False,
        )
        action_result = getattr(result, "action_result", None)
        if action_result is None:
            return None
        if str(getattr(action_result, "status", "") or "").strip() != "success":
            return None
        return action_result

    def _initialize_plan(
        self: Any,
        ctx: ExecutionContext,
        *,
        runtime: DefaultCodingLLMRuntime,
        model: str,
    ) -> tuple[CodingPlan, Any | None]:
        goal = (
            str(
                ctx.user_input
                or ctx.state.goal
                or getattr(ctx.decision, "objective", "")
                or ""
            ).strip()
            or "Complete the coding task."
        )
        response = runtime.complete(
            messages=[
                Message(
                    role="system",
                    content=self._build_plan_system_prompt(ctx),
                ),
                Message(role="user", content=goal),
            ],
            tools=[],
            model=model,
            tool_choice="auto",
            metadata=build_loop_thinking_metadata(ctx, purpose="plan"),
        )
        plan = self._plan_from_response(response=response, goal=goal)
        if plan is not None:
            self._apply_plan_to_scratchpad(plan)
            return plan, None
        fallback_plan = CodingPlan.fallback(goal)
        self._apply_plan_to_scratchpad(fallback_plan)
        seed_response = (
            response
            if (
                not bool(getattr(response, "ok", False))
                or list(getattr(response, "tool_calls", []) or [])
            )
            else None
        )
        return fallback_plan, seed_response

    def _build_plan_system_prompt(
        self: Any,
        ctx: ExecutionContext,
    ) -> str:
        parts = [
            "Return a JSON CodingPlan with fields goal, phases, current_phase, "
            "scratchpad, completed_steps, open_issues, subtasks, and optional "
            "verifier_goal. Use phases in order explore -> plan -> implement -> "
            "verify, or return a single implement phase.",
            "When you can state structural verification facts without guessing, "
            "populate verifier_goal with goal_id, description, success_criteria, "
            "deliverables, and optional failure_conditions using the typed Goal "
            "shape. Omit verifier_goal instead of inventing one.",
        ]
        repo_index = self._load_repo_index_context(ctx)
        if repo_index:
            parts.extend(("", "[REPO INDEX]", repo_index))
        else:
            repo_map = self._load_repo_map_context(ctx)
            if repo_map:
                parts.extend(("", "[REPO MAP - FALLBACK]", repo_map))
        return "\n".join(parts).strip()

    def _load_repo_index_context(self: Any, ctx: ExecutionContext) -> str:
        action_result = self._load_context_action_result(
            ctx,
            title="Load coding repo index",
            tool_name=MODEL_CODE_REPO_INDEX,
            args={"path": ".", "max_files": 40},
        )
        if action_result is None:
            return ""
        outputs = dict(getattr(action_result, "outputs", {}) or {})
        raw_index = outputs.get("repo_index")
        if not isinstance(raw_index, dict):
            return ""
        return self._render_repo_index_context(raw_index)

    def _load_repo_map_context(self: Any, ctx: ExecutionContext) -> str:
        action_result = self._load_context_action_result(
            ctx,
            title="Load coding repo map",
            tool_name=MODEL_CODE_REPO_MAP,
            args={"path": ".", "max_tokens": 2048},
        )
        if action_result is None:
            return ""
        outputs = dict(getattr(action_result, "outputs", {}) or {})
        for key in ("repo_map", "map", "content", "text"):
            text = str(outputs.get(key, "") or "").strip()
            if text:
                return text
        return str(getattr(action_result, "summary", "") or "").strip()

    def _render_repo_index_context(self: Any, raw_index: dict[str, Any]) -> str:
        lines: list[str] = []

        root = str(raw_index.get("root", "") or "").strip()
        if root:
            lines.append(f"root: {root}")

        files = list(raw_index.get("files") or [])
        if files:
            lines.append("files:")
            for file_row in files[:12]:
                if not isinstance(file_row, dict):
                    continue
                path = str(file_row.get("path", "") or "").strip()
                if not path:
                    continue
                language = str(file_row.get("language", "unknown") or "unknown").strip()
                symbols = ", ".join(
                    str(item).strip()
                    for item in list(file_row.get("top_level_symbols") or [])[:4]
                    if str(item).strip()
                )
                imports = ", ".join(
                    str(item).strip()
                    for item in list(file_row.get("imports") or [])[:4]
                    if str(item).strip()
                )
                detail_parts = [f"language={language}"]
                if symbols:
                    detail_parts.append(f"symbols={symbols}")
                if imports:
                    detail_parts.append(f"imports={imports}")
                lines.append(f"- {path} ({'; '.join(detail_parts)})")

        symbols = list(raw_index.get("symbols") or [])
        if symbols:
            lines.append("symbols:")
            for symbol_row in symbols[:12]:
                if not isinstance(symbol_row, dict):
                    continue
                name = str(symbol_row.get("name", "") or "").strip()
                if not name:
                    continue
                kind = str(symbol_row.get("kind", "unknown") or "unknown").strip()
                file_path = str(symbol_row.get("file", "") or "").strip()
                start_line = int(symbol_row.get("start_line") or 1)
                end_line = int(symbol_row.get("end_line") or start_line)
                lines.append(f"- {name} [{kind}] {file_path}:{start_line}-{end_line}")

        imports = list(raw_index.get("imports") or [])
        if imports:
            lines.append("imports:")
            for import_row in imports[:12]:
                if not isinstance(import_row, dict):
                    continue
                importer = str(import_row.get("importer", "") or "").strip()
                module = str(import_row.get("module", "") or "").strip()
                imported_names = ", ".join(
                    str(item).strip()
                    for item in list(import_row.get("imported_names") or [])[:4]
                    if str(item).strip()
                )
                if not importer or not module:
                    continue
                if imported_names:
                    lines.append(f"- {importer} -> {module} ({imported_names})")
                else:
                    lines.append(f"- {importer} -> {module}")

        return "\n".join(lines).strip()

    def _plan_from_response(
        self: Any,
        *,
        response: Any,
        goal: str,
    ) -> CodingPlan | None:
        if not bool(getattr(response, "ok", False)):
            return None
        if list(getattr(response, "tool_calls", []) or []):
            return None
        raw_text = str(getattr(response, "output_text", "") or "").strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.removeprefix("json").strip()
        if not raw_text:
            return None
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return coding_plan_from_payload(payload, goal=goal)

    def _apply_plan_to_scratchpad(self: Any, plan: CodingPlan) -> None:
        self._loop_state.scratchpad["coding.plan_phases_executed"] = [
            plan.current_phase
        ]
        self._loop_state.scratchpad["coding.current_phase"] = plan.current_phase
        self._loop_state.scratchpad["coding.open_issues_count"] = len(plan.open_issues)

    def _sync_plan_telemetry(self: Any) -> None:
        if self._coding_plan is None:
            return
        self._loop_state.scratchpad["coding.current_phase"] = (
            self._coding_plan.current_phase
        )
        self._loop_state.scratchpad["coding.open_issues_count"] = len(
            self._coding_plan.open_issues
        )

    def _emit_phase_status(self: Any, ctx: ExecutionContext) -> None:
        if self._coding_plan is None:
            return
        ctx.emit_status(
            source_phase="coding.plan",
            detail_text=f"{_CODING_PUBLIC_TAG} phase: {self._coding_plan.current_phase}",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state=self._coding_plan.current_phase,
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "coding.current_phase": self._coding_plan.current_phase,
                "coding.plan_phases_executed": list(
                    self._loop_state.scratchpad.get("coding.plan_phases_executed", [])
                    or []
                ),
                **self._resume_marker_payload(ctx),
            },
        )

    def _append_phase_instruction(self: Any) -> None:
        if self._coding_plan is None:
            return
        phase = self._coding_plan.current_phase_entry()
        self._loop_state.messages.append(
            Message(
                role="user",
                content=(
                    f"Continue the coding task in phase '{phase.name}'. "
                    f"Goal: {self._coding_plan.goal}. "
                    f"Steps: {', '.join(phase.steps) if phase.steps else 'advance this phase'}. "
                    f"Open issues: {', '.join(self._coding_plan.open_issues) if self._coding_plan.open_issues else 'none'}."
                ),
            )
        )
