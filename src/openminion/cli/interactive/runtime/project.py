from __future__ import annotations

import argparse
import shlex
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from openminion.modules.session.diagnostics.events import emit_session_operation

if TYPE_CHECKING:
    from openminion.cli.commands.autonomy_project import ProjectLaunchRequest


def _select_project_run(
    store: Any, tokens: list[str], session_id: str, agent_id: str
) -> Any:
    action = tokens[1]
    if len(tokens) == 2:
        if action != "status":
            raise ValueError("An exact RUN_ID is required.")
        runs = [
            run
            for run in store.list_runs(limit=None)
            if run.session_id == session_id
            and run.execution_selectors.agent_id == agent_id
        ]
        if len(runs) != 1:
            raise ValueError(
                "Choose an exact RUN_ID; this session has no single project."
            )
        run = runs[0]
    else:
        run = store.require(tokens[2])
    if run.session_id != session_id or run.execution_selectors.agent_id != agent_id:
        raise ValueError("Project belongs to another session or agent.")
    return run


def _project_control_args(runtime: Any) -> argparse.Namespace:
    from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH

    return argparse.Namespace(
        config=str(runtime.config_path),
        home_root=runtime.home_root,
        data_root=runtime.data_root,
        task_db=str(runtime.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH),
    )


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

        @property
        def permission_mode(self) -> str: ...

    def execute_project_control(self, line: str) -> tuple[str, str]:
        from openminion.cli.commands.autonomy_project import (
            cancel_project_task_wake,
            persisted_verification_waiver,
            project_task_manager,
            resume_project_run,
            schedule_unattended_project,
            workspace_path_from_ref,
        )
        from openminion.modules.task import AutonomyRunStore
        from openminion.modules.task.autonomy import (
            AutonomyRunPhase,
            AutonomyRunStatus,
            resolve_autonomy_state_root,
        )
        from openminion.modules.task.project import (
            ProjectControlAction,
            apply_project_control,
        )
        from openminion.modules.task.project.reports import (
            build_project_report_from_task,
            render_project_report,
        )

        if not self.is_bound:
            raise RuntimeError("No active session for /project.")
        tokens = shlex.split(line)
        if len(tokens) not in {2, 3} or tokens[1] not in {
            "status",
            "show",
            "pause",
            "resume",
            "cancel",
        }:
            raise ValueError(
                "usage: /project status [RUN_ID] | show/pause/resume/cancel RUN_ID"
            )
        action = tokens[1]
        store = AutonomyRunStore(root=resolve_autonomy_state_root(self._rt.home_root))
        run = _select_project_run(store, tokens, self.session_id, self.agent_id)
        args = _project_control_args(self._rt)
        manager = project_task_manager(args)
        try:
            if action in {"pause", "resume", "cancel"}:
                if run.status in {
                    AutonomyRunStatus.COMPLETED,
                    AutonomyRunStatus.CANCELLED,
                }:
                    raise ValueError(f"Project is already {run.status.value}.")
                if action == "pause":
                    apply_project_control(
                        manager, task_id=run.task_id, action=ProjectControlAction.PAUSE
                    )
                    run = store.transition(
                        run.run_id,
                        status=AutonomyRunStatus.BLOCKED,
                        phase=AutonomyRunPhase.CLOSED,
                        operator_summary="Project paused by operator.",
                        next_action_hint=f"Resume with /project resume {run.run_id}.",
                    )
                elif action == "resume":
                    run = resume_project_run(
                        manager,
                        store,
                        run,
                        workspace=workspace_path_from_ref(run.workspace_ref)
                        or Path(self.working_dir),
                        waiver=persisted_verification_waiver(run),
                    )
                    if run.status == AutonomyRunStatus.BLOCKED:
                        message = (
                            run.last_error.message
                            if run.last_error
                            else "verifier unavailable"
                        )
                        return "error", f"Project blocked: {message}"
                    run = schedule_unattended_project(args, store, manager, run)
                else:
                    cancel_project_task_wake(args, run, manager)
                    run = store.transition(
                        run.run_id,
                        status=AutonomyRunStatus.CANCELLED,
                        phase=AutonomyRunPhase.CLOSED,
                        operator_summary="Project cancelled by operator.",
                    )
            report = build_project_report_from_task(manager, task_id=run.task_id)
            report = report.model_copy(
                update={
                    "project_run": report.project_run.model_copy(
                        update={
                            "status": run.status,
                            "phase": run.phase,
                        }
                    )
                }
            )
            return "system", render_project_report(report)
        finally:
            manager.close()

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

    async def approve_project_handoff(
        self,
        metadata: Mapping[str, Any] | None,
        approval_callback: Callable[[str, dict[str, Any], Any], Awaitable[bool]] | None,
    ) -> str:
        if (
            metadata is None
            or "project_handoff" not in metadata
            or approval_callback is None
        ):
            return ""
        from openminion.cli.commands.autonomy_project import (
            build_project_launch_request,
            resolve_project_repository,
        )
        from openminion.modules.brain.adapters.session import SessctlAdapter
        from openminion.modules.brain.paths import resolve_brain_sessions_db_path
        from openminion.modules.brain.schemas.decisions import ProjectHandoff
        from openminion.modules.brain.state import consume_project_handoff

        handoff = ProjectHandoff.model_validate_json(metadata["project_handoff"])
        boundary = Path(self.working_dir)
        request = build_project_launch_request(
            goal=handoff.goal,
            session_id=self.session_id,
            agent_id=self.agent_id,
            workspace_boundary=boundary,
            repository=resolve_project_repository(boundary, handoff.repository or ""),
            require_git_repository=False,
            config_ref=str(self._rt.config_path),
            permission_profile_id=self.permission_mode,
            verification_commands=handoff.verification_commands,
            success_criteria=handoff.success_criteria,
            **handoff.model_dump(
                include={"max_iterations", "max_wall_clock_ms", "max_tool_calls"},
                exclude_none=True,
            ),
        )
        approved = await approval_callback(
            "project.start",
            self.project_launch_approval_args(request),
            request.run.run_id,
        )
        session_api = SessctlAdapter(
            resolve_brain_sessions_db_path(storage_path=self._rt.storage_path)
        )
        try:
            consumed = consume_project_handoff(
                session_api,
                session_id=self.session_id,
                agent_id=self.agent_id,
                handoff=handoff,
            )
        finally:
            session_api.close()
        if not consumed:
            return "Project proposal is no longer pending; nothing was launched."
        _, body = (
            self.launch_prepared_project(request)
            if approved
            else self.deny_prepared_project(request)
        )
        return body

    @staticmethod
    def project_launch_approval_args(
        request: ProjectLaunchRequest,
    ) -> dict[str, object]:
        run = request.run
        return {
            "run_id": run.run_id,
            "goal": run.goal_text,
            "success_criteria": list(request.success_criteria),
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
        try:
            error = verifier_preflight_error(
                request.run,
                workspace=request.repository,
                waiver=persisted_verification_waiver(request.run),
            )
            if error is not None:
                return self._block_project_without_verifier(
                    request, store, error, AutonomyRunStatus, AutonomyRunPhase
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
            self._record_project_launch(
                request, event_type="project.launched", status="ok"
            )
            return (
                "system",
                f"Project queued: {run.run_id}\n"
                f"Project: prun_{run.run_id}\n"
                f"Task: {run.task_id}\n"
                f"Workspace: {request.repository}\n"
                f"Next: {run.next_action_hint}",
            )
        finally:
            manager.close()

    def _block_project_without_verifier(
        self,
        request: ProjectLaunchRequest,
        store: Any,
        error: Any,
        status: Any,
        phase: Any,
    ) -> tuple[str, str]:
        store.create(request.run)
        blocked = store.transition(
            request.run.run_id,
            status=status.BLOCKED,
            phase=phase.CLOSED,
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
