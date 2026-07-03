"""Resume-state helpers for the coding strategy handler."""

from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_CONFIRM_RESPONSE_AFFIRM,
    BRAIN_CONFIRM_RESPONSE_DENY,
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_WAITING_USER,
    CODING_MODULE_STATE_KEY as _CODING_MODULE_STATE_KEY,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    STATE_KEY_MODULE_STATE,
    STATE_KEY_TASK_BACKED_RESUME,
)
from openminion.modules.brain.diagnostics.transitions import (
    set_status_unchecked,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.loop.tools.confirmation import (
    apply_session_confirmation_grant,
    confirmation_required_user_message,
    extract_confirmation_replay_queue,
    is_session_confirmation_response,
    strip_confirmation_replay_queue,
)
from openminion.modules.brain.loop.tools.messages import action_result_to_tool_message
from openminion.modules.brain.runner.tick.context import (
    _clear_pending_confirmation_metadata,
    _grant_once_from_confirmation,
    _parse_confirmation_response,
)
from openminion.modules.brain.schemas import refresh_command_identity, new_uuid
from openminion.modules.llm.schemas import Message
from .runtime import _build_blocked_result, _runner_and_profile_from_context


def _confirmation_replay_commands(confirmed: Any) -> list[Any]:
    return [strip_confirmation_replay_queue(confirmed)] + [
        strip_confirmation_replay_queue(queued)
        for queued in extract_confirmation_replay_queue(confirmed)
    ]


def _confirmation_grant_failed_response(
    ctx: ExecutionContext,
    confirmed: Any,
) -> ExecutionResult:
    ctx.state.pending_confirmation_command = confirmed
    return ExecutionResult.from_step_output(
        ctx.respond(
            message="I could not apply your confirmation yet. Please try again in a moment.",
            status=BRAIN_STATE_WAITING_USER,
            action_result=_build_blocked_result(
                "Confirmation grant could not be applied.",
                "confirmation_grant_failed",
            ),
        )
    )


class CodingResumeMixin:
    def _coding_module_state_payload(
        self: Any,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
        payload = module_state.get(_CODING_MODULE_STATE_KEY)
        return dict(payload) if isinstance(payload, dict) else {}

    def _resume_state_payload(
        self: Any,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        payload = self.snapshot_state()
        existing = dict(getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {})
        for key, value in existing.items():
            if key.startswith("_checkpoint_"):
                payload[key] = value
        checkpoint_id = str(
            getattr(ctx.state, "task_backed_checkpoint_id", "")
            or payload.get("last_checkpoint_id", "")
            or self._last_checkpoint_id
            or ""
        ).strip()
        if checkpoint_id:
            payload["last_checkpoint_id"] = checkpoint_id
            self._last_checkpoint_id = checkpoint_id
        payload["resume_count"] = int(payload.get("resume_count", 0) or 0)
        return payload

    def _prepare_resume_state(
        self: Any,
        ctx: ExecutionContext,
        *,
        payload: dict[str, Any],
        checkpoint_id: str | None,
    ) -> dict[str, Any]:
        prepared = dict(payload or {})
        if self._should_increment_resume_count(ctx, checkpoint_id=checkpoint_id):
            prepared["resume_count"] = int(prepared.get("resume_count", 0) or 0) + 1
        else:
            prepared["resume_count"] = int(prepared.get("resume_count", 0) or 0)
        normalized_checkpoint_id = str(
            checkpoint_id
            or prepared.get("last_checkpoint_id", "")
            or getattr(ctx.state, "task_backed_checkpoint_id", "")
            or ""
        ).strip()
        if normalized_checkpoint_id:
            prepared["last_checkpoint_id"] = normalized_checkpoint_id
            ctx.state.task_backed_checkpoint_id = normalized_checkpoint_id
        ctx.state.task_backed_resume_state = dict(prepared)
        restored = {
            key: value
            for key, value in prepared.items()
            if not key.startswith("_checkpoint_")
        }
        self.restore_state(restored)
        self._resume_prepared = True
        return prepared

    def _should_increment_resume_count(
        self: Any,
        ctx: ExecutionContext,
        *,
        checkpoint_id: str | None,
    ) -> bool:
        if str(checkpoint_id or "").strip():
            return True
        if ctx.state.pending_confirmation_command is not None:
            return True
        normalized_input = str(ctx.user_input or "").strip().lower()
        return normalized_input in {"continue", "resume", "yes", "y"}

    def _resume_marker_payload(
        self: Any,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self._resume_count > 0:
            payload["resume_count"] = int(self._resume_count)
        checkpoint_id = str(
            getattr(ctx.state, "task_backed_checkpoint_id", "")
            or self._last_checkpoint_id
            or ""
        ).strip()
        if checkpoint_id:
            payload["last_checkpoint_id"] = checkpoint_id
        return payload

    def _sync_coding_module_state(self: Any, ctx: ExecutionContext) -> None:
        if self._coding_plan is None:
            self._clear_coding_module_state(ctx)
            return
        module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
        payload = self._resume_state_payload(ctx)
        module_state[_CODING_MODULE_STATE_KEY] = payload
        ctx.state.module_state = module_state
        ctx.state.task_backed_resume_state = dict(payload)

    def _clear_coding_module_state(self: Any, ctx: ExecutionContext) -> None:
        module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
        module_state.pop(_CODING_MODULE_STATE_KEY, None)
        ctx.state.module_state = module_state
        ctx.state.task_backed_resume_state = {}
        self._resume_prepared = False

    def _apply_resume_input(self: Any, ctx: ExecutionContext) -> None:
        text = str(ctx.user_input or "").strip()
        if text:
            self._loop_state.messages.append(Message(role="user", content=text))

    def _append_confirmation_replay_continuation_marker(self: Any) -> None:
        self._loop_state.messages.append(
            Message(
                role="user",
                content=(
                    "The confirmed policy-replay tool batch above has already "
                    "executed successfully. Continue from those tool results; "
                    "do not repeat the same confirmed tool calls."
                ),
            )
        )

    def _record_replayed_command_result(
        self: Any,
        replay_command: Any,
        action_result: Any,
    ) -> None:
        self._record_verifier_candidate(replay_command, action_result)
        tool_name = str(getattr(replay_command, "tool_name", "") or "").strip()
        self._loop_state.append_tool_result(
            tool_name=tool_name,
            action_result=action_result,
        )
        self._loop_state.messages.append(
            action_result_to_tool_message(
                str(getattr(replay_command, "command_id", "") or ""),
                tool_name,
                action_result,
            )
        )
        if tool_name:
            self._loop_state.tool_calls_made.append(tool_name)
            self._loop_state.total_tool_calls += 1

    def _consume_pending_confirmation_reply(
        self: Any,
        ctx: ExecutionContext,
    ) -> ExecutionResult | None:
        command = getattr(ctx.state, "pending_confirmation_command", None)
        if command is None or ctx.user_input is None:
            return None
        runner, _profile = _runner_and_profile_from_context(ctx)
        user_reply = str(ctx.user_input or "")
        reply = _parse_confirmation_response(runner, user_reply)
        session_grant = is_session_confirmation_response(user_reply)
        if reply == BRAIN_CONFIRM_RESPONSE_DENY:
            ctx.state.pending_confirmation_command = None
            _clear_pending_confirmation_metadata(ctx.state)
            ctx.state.post_action_user_message = ""
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message="Confirmation denied. The coding action was not run.",
                    status=BRAIN_STATE_ERROR,
                    action_result=_build_blocked_result(
                        "Confirmation denied. The coding action was not run.",
                        "confirmation_denied",
                    ),
                )
            )
        if reply != BRAIN_CONFIRM_RESPONSE_AFFIRM and not session_grant:
            message = str(
                getattr(ctx.state, "post_action_user_message", "") or ""
            ).strip() or confirmation_required_user_message(command)
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=message,
                    status=BRAIN_STATE_WAITING_USER,
                    action_result=_build_blocked_result(
                        message,
                        "confirmation_unclear",
                    ),
                    kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
                )
            )
        confirmed = command.model_copy(deep=True)
        ctx.state.pending_confirmation_command = None
        replay_commands = _confirmation_replay_commands(confirmed)
        prepared_commands = []
        for replay_command in replay_commands:
            if session_grant:
                apply_session_confirmation_grant(ctx.state, replay_command)
            replay_inputs = (
                dict(replay_command.inputs)
                if isinstance(getattr(replay_command, "inputs", None), dict)
                else {}
            )
            grant_id, grant_supported = _grant_once_from_confirmation(
                runner,
                state=ctx.state,
                command=replay_command,
                logger=ctx.logger,
            )
            if grant_supported and not grant_id:
                return _confirmation_grant_failed_response(ctx, confirmed)
            replay_inputs["confirmation_source"] = "policy_replay"
            if grant_id:
                replay_inputs["confirmation_grant_id"] = grant_id
            elif not grant_supported:
                replay_inputs["confirmation_grant_id"] = (
                    f"local-confirmation-{new_uuid()}"
                )
            replay_command.inputs = replay_inputs
            prepared_commands.append(refresh_command_identity(replay_command))
        _clear_pending_confirmation_metadata(ctx.state)
        ctx.state.post_action_user_message = ""
        if ctx.state.status != BRAIN_STATE_ACTIVE:
            set_status_unchecked(
                ctx.state,
                BRAIN_STATE_ACTIVE,
                reason="coding_confirmation_replay",
            )
        for replay_command in prepared_commands:
            outcome = ctx.command_executor.execute_command(
                state=ctx.state,
                command=replay_command,
                logger=ctx.logger,
                include_reflect=False,
            )
            action_result = getattr(outcome, "action_result", None)
            if action_result is None:
                continue
            self._record_replayed_command_result(replay_command, action_result)
        self._append_confirmation_replay_continuation_marker()
        return None

    def _consume_seeded_confirmation_replay(
        self: Any,
        ctx: ExecutionContext,
    ) -> ExecutionResult | None:
        seeded_commands = list(getattr(ctx.decision, "_seeded_commands", []) or [])
        if not seeded_commands:
            return None
        reason_code = str(getattr(ctx.decision, "reason_code", "") or "").strip()
        if reason_code != "confirmation_replay":
            return None
        ctx.decision._seeded_commands = []
        _clear_pending_confirmation_metadata(ctx.state)
        ctx.state.post_action_user_message = ""
        if ctx.state.status != BRAIN_STATE_ACTIVE:
            set_status_unchecked(
                ctx.state,
                BRAIN_STATE_ACTIVE,
                reason="coding_seeded_confirmation_replay",
            )
        for replay_command in seeded_commands:
            outcome = ctx.command_executor.execute_command(
                state=ctx.state,
                command=replay_command,
                logger=ctx.logger,
                include_reflect=False,
            )
            action_result = getattr(outcome, "action_result", None)
            if action_result is None:
                continue
            self._record_replayed_command_result(replay_command, action_result)
            if (
                str(getattr(action_result, "status", "") or "").strip()
                == BRAIN_ACTION_STATUS_NEEDS_USER
            ):
                message = (
                    str(
                        getattr(ctx.state, "post_action_user_message", "") or ""
                    ).strip()
                    or str(getattr(action_result, "summary", "") or "").strip()
                )
                if not message:
                    message = "Policy confirmation required."
                return ExecutionResult.from_step_output(
                    ctx.respond(
                        message=message,
                        status=BRAIN_STATE_WAITING_USER,
                        action_result=action_result,
                        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
                    )
                )
        self._append_confirmation_replay_continuation_marker()
        return None
