from __future__ import annotations

from typing import Any, cast

from openminion.modules.brain.constants import (
    BRAIN_STATE_STOPPED,
    STATE_KEY_TASK_BACKED_RESUME,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.checkpoint.contracts import TaskProgress
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.execution.workflow import WorkflowPlan

from .contracts import CheckpointConsumer
from .manager import CheckpointManager, _normalized_text


class _CheckpointMixinBase:
    _checkpoint_task_id: str | None = None
    _checkpoint_manager: CheckpointManager | None = None
    _checkpoint_resuming: bool = False

    def _checkpoint_owner(self) -> str:
        owner = _normalized_text(getattr(self, "mode_name", ""))
        return owner or self.__class__.__name__.lower()

    def _checkpoint_goal(self, ctx: ExecutionContext) -> str:
        return (
            _normalized_text(getattr(ctx.state, "goal", "") or "")
            or _normalized_text(getattr(ctx.decision, "objective", "") or "")
            or _normalized_text(ctx.user_input or "")
            or self._checkpoint_owner()
        )

    def _checkpoint_task_metadata(self, ctx: ExecutionContext) -> dict[str, Any] | None:
        del ctx
        return None

    def _checkpoint_interval_value(self) -> int:
        configured = getattr(self, "_checkpoint_interval", None)
        if configured is not None:
            return max(1, int(configured))
        default_config = dict(getattr(self, "default_config", {}) or {})
        return max(1, int(default_config.get("checkpoint_interval", 1) or 1))

    def _checkpoint_cursor_from_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback: int = 0,
    ) -> int:
        for key in ("cursor", "next_iteration", "iteration"):
            value = payload.get(key)
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return max(0, int(fallback))

    def _checkpoint_manager_for(
        self, ctx: ExecutionContext
    ) -> CheckpointManager | None:
        if self._checkpoint_manager is not None:
            return self._checkpoint_manager
        runner = runner_from_context(ctx)
        task_manager = (
            getattr(runner, "task_manager", None) if runner is not None else None
        )
        if task_manager is None:
            return None
        self._checkpoint_manager = CheckpointManager(task_service=task_manager)
        return self._checkpoint_manager

    def _set_resume_state(
        self,
        ctx: ExecutionContext,
        *,
        payload: dict[str, Any],
        cursor: int,
    ) -> dict[str, Any]:
        resume_state = dict(payload or {})
        resume_state["_checkpoint_cursor"] = max(0, int(cursor))
        ctx.state.task_backed_resume_state = resume_state
        return resume_state

    def _init_checkpoint(self, ctx: ExecutionContext) -> str | None:
        current_task_id = _normalized_text(
            getattr(ctx.state, "task_backed_task_id", "")
            or self._checkpoint_task_id
            or ""
        )
        if current_task_id:
            self._checkpoint_task_id = current_task_id
            ctx.state.task_backed_task_id = current_task_id
            return current_task_id
        manager = self._checkpoint_manager_for(ctx)
        if manager is None:
            return None
        task_id = manager.create_task(
            session_id=ctx.state.session_id,
            owner=self._checkpoint_owner(),
            goal=self._checkpoint_goal(ctx),
            agent_id=ctx.state.agent_id,
            metadata=self._checkpoint_task_metadata(ctx),
        )
        self._checkpoint_task_id = task_id
        ctx.state.task_backed_task_id = task_id
        return task_id

    def _save_payload(
        self,
        ctx: ExecutionContext,
        *,
        payload: dict[str, Any],
        cursor: int,
    ) -> str | None:
        task_id = self._init_checkpoint(ctx)
        manager = self._checkpoint_manager_for(ctx)
        if not task_id or manager is None:
            self._set_resume_state(ctx, payload=payload, cursor=cursor)
            return None
        checkpoint_id = manager.save_payload(
            owner=self._checkpoint_owner(),
            version=int(getattr(self, "CHECKPOINT_VERSION", 1) or 1),
            task_id=task_id,
            payload=payload,
            cursor=cursor,
        )
        self._checkpoint_task_id = task_id
        ctx.state.task_backed_task_id = task_id
        ctx.state.task_backed_checkpoint_id = checkpoint_id
        self._set_resume_state(ctx, payload=payload, cursor=cursor)
        return checkpoint_id

    def _save_current_checkpoint(
        self,
        ctx: ExecutionContext,
        *,
        cursor: int = 0,
    ) -> str | None:
        consumer = cast(CheckpointConsumer, self)
        return self._save_payload(
            ctx,
            payload=consumer.snapshot_state(),
            cursor=max(0, int(cursor)),
        )

    def _load_resume_payload(
        self,
        ctx: ExecutionContext,
        checkpoint_id: str,
    ) -> dict[str, Any] | None:
        task_id = _normalized_text(
            getattr(ctx.state, "task_backed_task_id", "")
            or self._checkpoint_task_id
            or ""
        )
        if not task_id:
            return {"_resume_error": "Missing task-backed task ID."}
        manager = self._checkpoint_manager_for(ctx)
        if manager is None:
            return {}
        latest = manager.latest_raw_checkpoint(task_id=task_id)
        if latest is None:
            return {}
        latest_checkpoint_id, _ = latest
        if _normalized_text(checkpoint_id) and _normalized_text(
            latest_checkpoint_id
        ) != _normalized_text(checkpoint_id):
            return {
                "_resume_error": (
                    f"Checkpoint {checkpoint_id!r} is unavailable for task {task_id!r}."
                )
            }
        consumer = cast(CheckpointConsumer, self)
        envelope = manager.load(consumer=consumer, task_id=task_id)
        if envelope is None:
            return None
        ctx.state.task_backed_checkpoint_id = latest_checkpoint_id
        return self._set_resume_state(
            ctx,
            payload=dict(envelope.payload),
            cursor=int(envelope.cursor),
        )

    def _finalize_checkpoint(
        self,
        ctx: ExecutionContext,
        *,
        terminal: bool,
        cursor: int = 0,
    ) -> str | None:
        checkpoint_id = self._save_current_checkpoint(ctx, cursor=max(0, int(cursor)))
        task_id = _normalized_text(
            getattr(ctx.state, "task_backed_task_id", "")
            or self._checkpoint_task_id
            or ""
        )
        manager = self._checkpoint_manager_for(ctx)
        if task_id and manager is not None:
            manager.transition_task(
                task_id=task_id,
                to_state="done" if terminal else "paused",
            )
        return checkpoint_id

    def checkpoint(self, ctx: ExecutionContext, state: dict[str, Any]) -> str:
        payload = dict(state or {})
        cursor = self._checkpoint_cursor_from_payload(
            payload,
            fallback=int(getattr(ctx.state, "cursor", 0) or 0),
        )
        return self._save_payload(ctx, payload=payload, cursor=cursor) or ""

    def report_progress(self, ctx: ExecutionContext, progress: TaskProgress) -> None:
        task_id = _normalized_text(
            getattr(ctx.state, "task_backed_task_id", "")
            or self._checkpoint_task_id
            or ""
        )
        if not task_id:
            return
        payload = progress.model_dump(mode="json", exclude_none=True)
        ctx.update_task_progress(task_id=task_id, progress=payload)
        if progress.message:
            ctx.emit_status(
                source_phase="ACT",
                runtime_status="active",
                detail_text=progress.message,
                mode=self._checkpoint_owner(),
                mode_state=progress.phase,
                payload={"completion_pct": progress.completion_pct},
            )

    def emit_partial_result(self, ctx: ExecutionContext, result: str) -> None:
        text = _normalized_text(result)
        if not text:
            return
        ctx.emit_status(
            source_phase="ACT",
            runtime_status="active",
            detail_text=text,
            mode=self._checkpoint_owner(),
            mode_state="partial_result",
            payload={"partial_result": text},
        )

    def cancel(self, ctx: ExecutionContext, reason: str) -> ExecutionResult:
        self._finalize_checkpoint(
            ctx,
            terminal=False,
            cursor=int(getattr(ctx.state, "cursor", 0) or 0),
        )
        task_id = _normalized_text(
            getattr(ctx.state, "task_backed_task_id", "")
            or self._checkpoint_task_id
            or ""
        )
        manager = self._checkpoint_manager_for(ctx)
        if task_id and manager is not None:
            manager.transition_task(task_id=task_id, to_state="cancelled")
        ctx.state.status = BRAIN_STATE_STOPPED
        message = _normalized_text(reason) or f"{self._checkpoint_owner()} cancelled."
        return ExecutionResult.from_step_output(
            ctx.respond(message=message, status=BRAIN_STATE_STOPPED)
        )


class CheckpointMixin(_CheckpointMixinBase):
    def _save_checkpoint(self, ctx: ExecutionContext, *, cursor: int) -> str | None:
        if max(0, int(cursor)) % self._checkpoint_interval_value() != 0:
            consumer = cast(CheckpointConsumer, self)
            self._set_resume_state(
                ctx,
                payload=consumer.snapshot_state(),
                cursor=max(0, int(cursor)),
            )
            return None
        return self._save_current_checkpoint(ctx, cursor=max(0, int(cursor)))

    def _try_resume(self, ctx: ExecutionContext) -> WorkflowPlan | None:
        resume_state = dict(getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {})
        if not resume_state:
            task_id = _normalized_text(
                getattr(ctx.state, "task_backed_task_id", "")
                or self._checkpoint_task_id
                or ""
            )
            manager = self._checkpoint_manager_for(ctx)
            if task_id and manager is not None:
                consumer = cast(CheckpointConsumer, self)
                envelope = manager.load(consumer=consumer, task_id=task_id)
                if envelope is not None:
                    resume_state = self._set_resume_state(
                        ctx,
                        payload=dict(envelope.payload),
                        cursor=int(envelope.cursor),
                    )
        if not resume_state:
            return None
        if "_resume_error" in resume_state:
            return None
        cursor = max(0, int(resume_state.get("_checkpoint_cursor", 0) or 0))
        payload = {
            key: value
            for key, value in resume_state.items()
            if not key.startswith("_checkpoint_")
        }
        consumer = cast(CheckpointConsumer, self)
        consumer.restore_state(payload)
        self._checkpoint_resuming = True
        try:
            workflow = self.initialize(ctx)
        finally:
            self._checkpoint_resuming = False
        workflow.cursor = min(cursor, len(workflow.steps))
        ctx.state.cursor = workflow.cursor
        return workflow

    def resume(
        self,
        ctx: ExecutionContext,
        checkpoint_id: str | None = None,
    ) -> WorkflowPlan | dict[str, Any] | None:
        if checkpoint_id is None:
            return self._try_resume(ctx)
        payload = self._load_resume_payload(ctx, checkpoint_id)
        return payload or {}


class SimpleCheckpointMixin(_CheckpointMixinBase):
    def _save_checkpoint(self, ctx: ExecutionContext, *, cursor: int = 0) -> str | None:
        return self._save_current_checkpoint(ctx, cursor=max(0, int(cursor)))

    def resume(self, ctx: ExecutionContext, checkpoint_id: str) -> dict[str, Any]:
        payload = self._load_resume_payload(ctx, checkpoint_id)
        return payload or {}


__all__ = ["CheckpointMixin", "SimpleCheckpointMixin"]
