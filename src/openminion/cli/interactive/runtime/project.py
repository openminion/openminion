from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.modules.session.diagnostics.events import emit_session_operation

if TYPE_CHECKING:
    from openminion.cli.commands.autonomy_project import ProjectLaunchRequest


class RuntimeProjectMixin:
    _rt: Any

    if TYPE_CHECKING:

        @property
        def agent_id(self) -> str: ...

        @property
        def is_bound(self) -> bool: ...

        @property
        def session_id(self) -> str: ...

        @property
        def working_dir(self) -> str: ...

    def prepare_project_command(self, line: str) -> ProjectLaunchRequest:
        if not self.is_bound:
            raise RuntimeError("No active session for /project.")
        from openminion.cli.commands.autonomy_project import parse_focus_project_launch

        return parse_focus_project_launch(
            line,
            session_id=self.session_id,
            agent_id=self.agent_id,
            workspace_boundary=Path(self.working_dir),
            config_ref=str(self._rt.config_path),
        )

    @staticmethod
    def project_launch_approval_args(
        request: ProjectLaunchRequest,
    ) -> dict[str, object]:
        run = request.run
        return {
            "run_id": run.run_id,
            "goal": run.goal_text,
            "workspace_boundary": str(request.workspace_boundary),
            "repository": str(request.repository),
            "expected_checks": list(request.expected_checks),
            "release_tools": request.release_tools,
            "permission_profile_id": run.permission_profile_id,
            "max_iterations": run.continuation_policy.max_iterations,
            "max_wall_clock_ms": run.continuation_policy.max_wall_clock_ms,
            "max_tool_calls": run.continuation_policy.max_tool_calls,
            "require_operator_after_blocked": (
                run.continuation_policy.require_operator_after_blocked
            ),
            "verification_domain": run.execution_selectors.verification_domain,
            "verifier_ref": run.execution_selectors.verifier_ref,
            "verification_commands": list(
                run.execution_selectors.verification_commands
            ),
            "verification_waiver_reason": (
                run.execution_selectors.verification_waiver_reason
            ),
            "turn_timeout_seconds": run.execution_selectors.turn_timeout_seconds,
            "verification_timeout_seconds": (
                run.execution_selectors.verification_timeout_seconds
            ),
        }

    def launch_prepared_project(self, request: ProjectLaunchRequest) -> tuple[str, str]:
        from openminion.cli.commands.autonomy_project import (
            configured_cron_store,
            launch_project,
            persisted_verification_waiver,
            schedule_project_wake,
            verifier_preflight_error,
        )
        from openminion.modules.task import AutonomyRunStore, TaskManager
        from openminion.modules.task.autonomy import (
            AutonomyRunPhase,
            AutonomyRunStatus,
            resolve_autonomy_state_root,
        )
        from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
        from openminion.modules.task.runtime.lifecycle import TaskLifecycleState

        store = AutonomyRunStore(root=resolve_autonomy_state_root(self._rt.home_root))
        database = (self._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
        manager = TaskManager.for_lifecycle_db(db_path=database)
        error = verifier_preflight_error(
            request.run,
            workspace=request.repository,
            waiver=persisted_verification_waiver(request.run),
        )
        if error is not None:
            store.create(request.run)
            blocked = store.transition(
                request.run.run_id,
                status=AutonomyRunStatus.BLOCKED,
                phase=AutonomyRunPhase.CLOSED,
                operator_summary="Project blocked before execution.",
                next_action_hint="Configure a verifier, then rerun `/project start`.",
                error=error,
            )
            self._record_project_launch(
                request,
                event_type="project.launch_blocked",
                status="blocked",
                reason_code=error.code,
            )
            return (
                "error",
                f"Project blocked: {error.message}\n"
                f"Run: {blocked.run_id}\n"
                f"Next: {blocked.next_action_hint}",
            )

        run = launch_project(request, store=store, manager=manager)
        cron_store = None
        try:
            cron_store = configured_cron_store(
                argparse.Namespace(
                    config=str(self._rt.config_path),
                    home_root=self._rt.home_root,
                    data_root=self._rt.data_root,
                ),
                config_ref=run.execution_selectors.config_ref,
            )
            run = schedule_project_wake(
                cron_store=cron_store,
                store=store,
                manager=manager,
                run=run,
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            if run.task_id:
                manager.transition_task(
                    task_id=run.task_id,
                    to_state=TaskLifecycleState.PAUSED,
                )
            blocked = store.transition(
                run.run_id,
                status=AutonomyRunStatus.BLOCKED,
                phase=AutonomyRunPhase.CLOSED,
                operator_summary="Project wake could not be scheduled.",
                next_action_hint=(
                    f"Resume with `openminion autonomy resume {run.run_id} "
                    "--unattended`."
                ),
            )
            self._record_project_launch(
                request,
                event_type="project.launch_blocked",
                status="blocked",
                reason_code="wake_schedule_failed",
            )
            return ("error", f"Project blocked: {exc}\n{blocked.next_action_hint}")
        finally:
            if cron_store is not None:
                cron_store.close()
        self._record_project_launch(request, event_type="project.launched", status="ok")
        return (
            "system",
            f"Project queued: {run.run_id}\n"
            f"Project: prun_{run.run_id}\n"
            f"Task: {run.task_id}\n"
            f"Workspace: {request.repository}\n"
            f"Next: {run.next_action_hint}",
        )

    def deny_prepared_project(self, request: ProjectLaunchRequest) -> tuple[str, str]:
        self._record_project_launch(
            request,
            event_type="project.launch_denied",
            status="denied",
            reason_code="operator_denied",
        )
        return ("error", "Project launch denied.")

    def _record_project_launch(
        self,
        request: ProjectLaunchRequest,
        *,
        event_type: str,
        status: str,
        reason_code: str | None = None,
    ) -> None:
        run = request.run
        payload = {
            "autonomy_run_id": run.run_id,
            "project_run_id": f"prun_{run.run_id}",
            "goal_id": run.goal_id,
            "workspace_boundary": str(request.workspace_boundary),
            "execution_repository": str(request.repository),
            **({"reason_code": reason_code} if reason_code else {}),
        }
        self._rt.sessions.append_event(
            session_id=self.session_id,
            event_type=event_type,
            actor_type="human" if reason_code else "system",
            actor_id="operator" if reason_code else self.agent_id,
            task_id=run.task_id,
            payload=payload,
            status=status,
            redaction="bounded",
        )
        emit_session_operation(
            telemetryctl=self._rt.telemetry_service,
            session_id=self.session_id,
            turn_id=f"project-launch:{run.run_id}",
            operation=("project_launch_denied" if reason_code else "project_launch"),
            status=status,
            extra=payload,
        )
