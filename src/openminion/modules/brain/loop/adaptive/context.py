import re
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
)
from openminion.modules.brain.execution.intent_state import (
    remaining_intent_ids,
    succeeded_intent_ids,
    update_intent_execution_states,
)
from openminion.modules.brain.tools.parser import (
    explicit_tool_name_sequence,
    normalize_tool_name_for_brain,
    parse_tool_command,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    build_intent_execution_states,
    new_uuid,
)
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopState,
    DirectToolTurnContext,
    build_loop_thinking_metadata,
    semantic_batch_signature,
)
from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
)
from openminion.modules.brain.loop.tools.direct_reasons import (
    is_explicit_direct_tool_reason,
)
from openminion.modules.brain.loop.tools.iteration.helpers import (
    _execute_prepared_tool_dispatch_from_context,
    _finalize_tool_result_from_context,
)
from openminion.modules.llm.schemas import ToolCall
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.brain.trailers import (
    EXPECTED_TRAILERS_METADATA_KEY,
    TRAILER_LANE_MACC,
    TRAILER_LANE_SWSC,
)

from ..services import runner_from_context


def _adaptive_loop_metadata(ctx: ExecutionContext, *, purpose: str) -> dict[str, Any]:
    metadata = build_loop_thinking_metadata(ctx, purpose=purpose)
    if purpose == "act":
        metadata[EXPECTED_TRAILERS_METADATA_KEY] = [
            TRAILER_LANE_MACC,
            TRAILER_LANE_SWSC,
        ]
    return metadata


_EXPLICIT_TOOL_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9_.-]*\b")
_EXACT_TOOL_BATCH_RE = re.compile(
    r"(?is)\b(?:first|initial|required)\s+tool\s+batch\b.*?(?:[.!?](?:\s|$)|\n|$)"
)


def _collapse_repeated_single_tool_mentions(
    tool_names: tuple[str, ...],
) -> tuple[str, ...]:
    if not tool_names:
        return ()
    if len(set(tool_names)) == 1:
        return (tool_names[0],)
    return tool_names


def _explicit_tool_name_mentions(user_text: str) -> tuple[str, ...]:
    matches: list[str] = []
    for token in _EXPLICIT_TOOL_TOKEN_RE.findall(str(user_text or "").strip().lower()):
        canonical = normalize_tool_name_for_brain(token)
        if not canonical:
            continue
        if canonical not in matches:
            matches.append(canonical)
    return tuple(matches)


def _looks_like_direct_tool_instruction(user_text: str) -> bool:
    text = str(user_text or "").strip()
    if not text:
        return False
    return text.lower().startswith("tool ")


def _exact_first_tool_batch_sequence(user_text: str) -> tuple[str, ...]:
    text = str(user_text or "")
    match = _EXACT_TOOL_BATCH_RE.search(text)
    if match is None:
        return ()
    return explicit_tool_name_sequence(match.group(0))


def _direct_tool_turn_context(
    *,
    ctx: ExecutionContext,
    seed_response: Any | None,
) -> DirectToolTurnContext | None:
    seeded_commands = list(getattr(ctx.decision, "_seeded_commands", []) or [])
    decision_reason_code = str(getattr(ctx.decision, "reason_code", "") or "").strip()
    if seeded_commands and is_explicit_direct_tool_reason(decision_reason_code):
        requested_calls: list[ToolCall] = []
        requested_tool_names: list[str] = []
        for seeded_command in seeded_commands:
            seeded_tool_name = str(
                getattr(seeded_command, "tool_name", "") or ""
            ).strip()
            seeded_args = dict(getattr(seeded_command, "args", {}) or {})
            seeded_inputs = getattr(seeded_command, "inputs", None)
            if not seeded_tool_name:
                continue
            if isinstance(seeded_inputs, dict) and seeded_inputs:
                requested_calls.append(
                    SimpleNamespace(
                        name=seeded_tool_name,
                        arguments=seeded_args,
                        inputs=dict(seeded_inputs),
                    )
                )
            else:
                requested_calls.append(
                    ToolCall(
                        name=seeded_tool_name,
                        arguments=seeded_args,
                    )
                )
            requested_tool_names.append(seeded_tool_name)
        requested_batch_signature = semantic_batch_signature(requested_calls)
        if requested_tool_names and requested_batch_signature:
            return DirectToolTurnContext(
                requested_tool_names=tuple(requested_tool_names),
                requested_batch_signature=requested_batch_signature,
                requested_calls=tuple(requested_calls),
            )
    runner = runner_from_context(ctx)
    if runner is not None and str(ctx.user_input or "").strip():
        parsed_command = parse_tool_command(
            runner=runner,
            state=ctx.state,
            text=str(ctx.user_input or ""),
        )
        if parsed_command is not None:
            requested_tool_name = str(
                getattr(parsed_command, "tool_name", "") or ""
            ).strip()
            requested_args = dict(getattr(parsed_command, "args", {}) or {})
            if requested_tool_name:
                requested_call = ToolCall(
                    name=requested_tool_name,
                    arguments=requested_args,
                )
                requested_batch_signature = semantic_batch_signature([requested_call])
                if requested_batch_signature:
                    return DirectToolTurnContext(
                        requested_tool_names=(requested_tool_name,),
                        requested_batch_signature=requested_batch_signature,
                        requested_calls=(requested_call,),
                    )
    user_text = str(ctx.user_input or "").strip()
    exact_batch_sequence = _exact_first_tool_batch_sequence(user_text)
    if len(exact_batch_sequence) >= 2:
        return DirectToolTurnContext(
            requested_tool_names=exact_batch_sequence,
            requested_batch_signature="",
            requested_calls=(),
            match_by_name_only=True,
        )
    seed_tool_calls = list(getattr(seed_response, "tool_calls", []) or [])
    if user_text and len(seed_tool_calls) == 1:
        seed_tool_call = seed_tool_calls[0]
        seed_tool_name = str(getattr(seed_tool_call, "name", "") or "").strip()
        if (
            seed_tool_name
            and seed_tool_name in user_text
            and _looks_like_direct_tool_instruction(user_text)
        ):
            requested_batch_signature = semantic_batch_signature([seed_tool_call])
            if requested_batch_signature:
                return DirectToolTurnContext(
                    requested_tool_names=(seed_tool_name,),
                    requested_batch_signature=requested_batch_signature,
                    requested_calls=(seed_tool_call,),
                )
    explicit_sequence = explicit_tool_name_sequence(user_text)
    if explicit_sequence and _looks_like_direct_tool_instruction(user_text):
        return DirectToolTurnContext(
            requested_tool_names=_collapse_repeated_single_tool_mentions(
                explicit_sequence
            ),
            requested_batch_signature="",
            requested_calls=(),
            match_by_name_only=True,
        )
    explicit_mentions = _explicit_tool_name_mentions(user_text)
    if len(explicit_mentions) == 1 and _looks_like_direct_tool_instruction(user_text):
        return DirectToolTurnContext(
            requested_tool_names=explicit_mentions,
            requested_batch_signature="",
            requested_calls=(),
            match_by_name_only=True,
        )
    return None


class _AdaptiveLoopContextAdapter:
    def __init__(self, ctx: ExecutionContext) -> None:
        self.state = ctx.state
        self._ctx = ctx
        self._runner = runner_from_context(ctx) or SimpleNamespace(
            options=SimpleNamespace(failure_strategy="halt")
        )
        self._intent_step_index = 0

    def execute_command(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ):
        command = self._attach_current_intent(command)
        outcome = self._ctx.command_executor.execute_command(
            state=self._ctx.state,
            command=command,
            logger=self._ctx.logger,
            include_reflect=include_reflect,
        )
        return self._postprocess_outcome(outcome, original_command=command)

    def prepare_tool_dispatch(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ):
        command = self._attach_current_intent(command)
        prepare_fn = getattr(self._ctx.command_executor, "prepare_tool_dispatch", None)
        if callable(prepare_fn):
            return prepare_fn(
                state=self._ctx.state,
                command=command,
                logger=self._ctx.logger,
                include_reflect=include_reflect,
            )
        from openminion.modules.brain.loop.tools.contracts import (
            PreparedToolDispatch,
        )  # noqa: PLC0415

        return PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=str(getattr(command, "command_id", "") or new_uuid()),
            tool_name=str(getattr(command, "tool_name", "") or "").strip(),
            validated_args=dict(getattr(command, "args", {}) or {}),
            session_id=str(getattr(self._ctx.state, "session_id", "") or ""),
            trace_id=str(getattr(self._ctx.state, "trace_id", "") or ""),
            agent_id=str(getattr(self._ctx.state, "agent_id", "") or ""),
            lineage={},
            permission_mode=str(
                getattr(self._ctx.state, "permission_mode", "default") or "default"
            ),
            payload={},
        )

    def execute_prepared_tool_dispatch(
        self,
        *,
        prepared_dispatch,
    ):
        return _execute_prepared_tool_dispatch_from_context(
            self._ctx,
            prepared_dispatch=prepared_dispatch,
        )

    def finalize_tool_result(
        self,
        *,
        prepared_dispatch,
        raw_result,
    ):
        return _finalize_tool_result_from_context(
            self._ctx,
            prepared_dispatch=prepared_dispatch,
            raw_result=raw_result,
            postprocess_outcome=self._postprocess_outcome,
        )

    def finalize_prepare_outcome(
        self,
        *,
        prepare_outcome,
    ):
        from openminion.modules.brain.loop.tools.contracts import (
            CommandExecutionOutcome,
        )  # noqa: PLC0415

        outcome = CommandExecutionOutcome(
            approved_command=prepare_outcome.approved_command,
            action_result=prepare_outcome.action_result,
        )
        return self._postprocess_outcome(
            outcome,
            original_command=getattr(prepare_outcome, "original_command", None),
        )

    def emit_status(self, **kwargs) -> None:
        self._ctx.emit_status(**kwargs)

    def advance_after_action(
        self,
        *,
        action_result: ActionResult,
        force_replan: bool = False,
    ) -> None:
        self._ctx.command_executor.advance_after_action(
            state=self._ctx.state,
            action_result=action_result,
            force_replan=force_replan,
            logger=self._ctx.logger,
        )

    def _postprocess_outcome(
        self, outcome: Any, *, original_command: Any | None
    ) -> Any:
        approved_command = getattr(outcome, "approved_command", original_command)
        action_result = getattr(outcome, "action_result", None)
        if (
            str(getattr(approved_command, "kind", "") or "").strip().lower()
            == "ask_user"
        ):
            from openminion.modules.brain.runner.tick.context import (
                _store_pending_confirmation_metadata,
            )  # noqa: PLC0415

            pending_command = (
                original_command.model_copy(deep=True)
                if original_command is not None
                else approved_command.model_copy(deep=True)
            )
            self.state.pending_confirmation_command = pending_command
            _store_pending_confirmation_metadata(self.state)
        if (
            action_result is not None
            and str(getattr(action_result, "status", "") or "").strip() == "needs_user"
        ):
            raw_error = getattr(action_result, "error", None)
            if isinstance(raw_error, dict):
                error_code = str(raw_error.get("code", "") or "")
            else:
                error_code = str(getattr(raw_error, "code", "") or "")
            if error_code.strip().upper() == TOOL_ERROR_CONFIRM_REQUIRED:
                from openminion.modules.brain.runner.tick.context import (
                    _store_pending_confirmation_metadata,
                )  # noqa: PLC0415

                self.state.pending_confirmation_command = approved_command.model_copy(
                    deep=True
                )
                _store_pending_confirmation_metadata(self.state)
                self.state.post_action_user_message = (
                    confirmation_required_user_message(approved_command)
                )
        if action_result is not None and getattr(
            self.state, "intent_execution_states", []
        ):
            update_intent_execution_states(
                self._runner,
                state=self.state,
                command=approved_command,
                action_result=action_result,
                current_step_index=self._intent_step_index,
            )
            self._intent_step_index += 1
        return outcome

    def _attach_current_intent(self, command: Any) -> Any:
        if command is None or not getattr(self.state, "intent_execution_states", []):
            return command
        current_ids = list(getattr(command, "sub_intent_ids", []) or [])
        if current_ids:
            return command
        next_intent_id = self._current_pending_intent_id()
        if not next_intent_id:
            return command
        try:
            return command.model_copy(
                update={"sub_intent_ids": [next_intent_id]},
                deep=True,
            )
        except Exception:  # noqa: BLE001
            return command

    def _current_pending_intent_id(self) -> str:
        for item in list(getattr(self.state, "intent_execution_states", []) or []):
            if str(getattr(item, "status", "") or "").strip() != "succeeded":
                return str(getattr(item, "intent_id", "") or "").strip()
        return ""


def _sync_adaptive_intent_tracking(
    *,
    ctx: ExecutionContext,
    loop_state: AdaptiveToolLoopState | None = None,
) -> tuple[list[str], list[str]]:
    if not getattr(ctx.state, "intent_execution_states", []) and getattr(
        ctx.state, "decision_sub_intent_refs", []
    ):
        ctx.state.intent_execution_states = build_intent_execution_states(
            list(getattr(ctx.state, "decision_sub_intent_refs", []) or []),
            existing=[],
        )
    completed_ids = succeeded_intent_ids(
        list(getattr(ctx.state, "intent_execution_states", []) or [])
    )
    remaining_ids = remaining_intent_ids(
        list(getattr(ctx.state, "intent_execution_states", []) or [])
    )
    ctx.state.adaptive_satisfied_intent_ids = list(completed_ids)
    if loop_state is not None:
        loop_state.scratchpad["completed_intent_ids"] = list(completed_ids)
        loop_state.scratchpad["remaining_intent_ids"] = list(remaining_ids)
    return completed_ids, remaining_ids
