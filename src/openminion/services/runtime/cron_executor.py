from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.logging import format_structured_event, get_logger
from openminion.services.runtime.routine_context import (
    CronRunRoutineSink,
    ToolRegistryPreTurnContext,
)
from openminion.tools.task.routine.dispatcher import (
    RoutineDispatcher,
    build_default_dispatcher,
)
from openminion.tools.task.routine.schemas import RoutinePayloadV1
from openminion.services.runtime.cron_audit import watch_write_audit_entries
from openminion.tools.task.constants import (
    CONSOLIDATION_PAYLOAD_KEY,
    DEFAULT_CONSOLIDATION_BATCH_LIMIT,
    DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
    DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_WATCH_MAX_CHECKS,
    DEFAULT_WATCH_MAX_ITERATIONS,
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    DEFAULT_WATCH_TTL_MINUTES,
    WATCH_DEFAULT_ALLOWED_TOOLS,
    WATCH_PAYLOAD_KEY,
    WATCH_TURN_KIND_ACTION,
    WATCH_TURN_KIND_CHECK,
)

_CRON_LOGGER = get_logger("modules.cron")


class CronTurnExecutor:
    """Execute daemon-hosted cron runs without closure-captured state."""

    def __init__(
        self,
        *,
        runtime: Any,
        cron_store: Any,
        request_builder: Callable[[dict[str, Any], str], Any],
        timeout_s: float,
        max_attempts: int,
        routine_dispatcher: RoutineDispatcher | None = None,
    ) -> None:
        self._runtime = runtime
        self._cron_store = cron_store
        self._request_builder = request_builder
        self._timeout_s = max(10.0, float(timeout_s))
        self._max_attempts = max(1, int(max_attempts))
        self._routine_dispatcher = routine_dispatcher or build_default_dispatcher()

    def execute(self, job: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self._runtime, "runtime_manager", None):
            return {"summary": "no runtime manager", "error": True}

        payload = job.get("payload", {})
        if (
            payload.get("kind") == "systemEvent"
            and payload.get("event_text") == "prune_cron_runs"
        ):
            return self._execute_system_event(payload)

        payload = job.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("kind") == "agentIdleTick":
            return self._execute_idle_tick_turn(job=job, run=run, payload=payload)
        watch = self._watch_metadata(payload)
        if watch is not None:
            routine = self._routine_dispatcher.routine_for(payload)
            if routine is not None:
                return self._execute_routine_turn(
                    job=job,
                    run=run,
                    payload=payload,
                    watch=watch,
                    routine=routine,
                )
            return self._execute_watch_turn(
                job=job, run=run, payload=payload, watch=watch
            )

        return self._execute_agent_turn(job=job, run=run, payload=payload)

    def _execute_watch_turn(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        payload: dict[str, Any],
        watch: dict[str, Any],
    ) -> dict[str, Any]:
        expired_precheck = self._watch_ttl_expired(job=job, watch=watch)
        current_checks = int(watch.get("checks_completed", 0) or 0)
        if expired_precheck:
            summary = self._watch_terminal_summary(
                watch=watch,
                checks_completed=current_checks,
                terminal_reason="ttl_expired",
                fallback="Watch expired before the next check ran.",
            )
            return {
                "summary": summary,
                "output": self._watch_output(
                    condition_met=False,
                    terminal=True,
                    deliver=True,
                    checks_completed=current_checks,
                    terminal_reason="ttl_expired",
                    summary=summary,
                ),
            }

        result = self._execute_agent_turn(job=job, run=run, payload=payload)
        checks_completed = current_checks + 1
        metadata = dict(result.get("metadata") or {})
        watch_condition_met = (
            str(metadata.get("watch_condition_met", "") or "").strip().lower() == "true"
        )
        watch_summary = str(metadata.get("watch_summary", "") or "").strip()
        summary = str(result.get("summary", "") or "").strip() or watch_summary
        action_executed = False
        action_summary = ""
        write_audit_entries: tuple[dict[str, Any], ...] = ()
        terminal_reason = ""
        deliver = bool(watch_condition_met)
        terminal = bool(watch_condition_met)
        if checks_completed >= int(
            watch.get("max_checks", DEFAULT_WATCH_MAX_CHECKS)
            or DEFAULT_WATCH_MAX_CHECKS
        ):
            terminal = True
            deliver = True
            terminal_reason = "max_checks_reached"
            summary = self._watch_terminal_summary(
                watch=watch,
                checks_completed=checks_completed,
                terminal_reason=terminal_reason,
                fallback=summary,
            )
        elif self._watch_ttl_expired(job=job, watch=watch):
            terminal = True
            deliver = True
            terminal_reason = "ttl_expired"
            summary = self._watch_terminal_summary(
                watch=watch,
                checks_completed=checks_completed,
                terminal_reason=terminal_reason,
                fallback=summary,
            )
        elif watch_condition_met:
            terminal_reason = "condition_met"
            action_result = self._execute_watch_action_turn(
                job=job,
                run=run,
                payload=payload,
                watch=watch,
                watch_summary=summary,
            )
            if action_result is not None:
                action_executed = True
                action_summary = str(action_result.get("summary", "") or "").strip()
                if action_summary:
                    summary = action_summary
                write_audit_entries = watch_write_audit_entries(
                    metadata=dict(action_result.get("metadata") or {}),
                )

        self._persist_watch_progress(
            job=job,
            payload=payload,
            watch=watch,
            checks_completed=checks_completed,
            summary=summary,
            condition_met=watch_condition_met,
            terminal_reason=terminal_reason,
            write_audit_entries=write_audit_entries
            if bool(watch.get("write_authorized", False))
            else (),
        )
        output = self._watch_output(
            condition_met=watch_condition_met,
            terminal=terminal,
            deliver=deliver,
            checks_completed=checks_completed,
            terminal_reason=terminal_reason,
            summary=summary,
            action_executed=action_executed,
            action_summary=action_summary,
        )
        result["summary"] = summary
        result["output"] = output
        return result

    def _execute_watch_action_turn(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        payload: dict[str, Any],
        watch: dict[str, Any],
        watch_summary: str,
    ) -> dict[str, Any] | None:
        action_instruction = str(watch.get("on_condition_action", "") or "").strip()
        if not action_instruction:
            return None
        action_watch = dict(watch)
        action_watch["turn_kind"] = WATCH_TURN_KIND_ACTION
        action_payload = dict(payload)
        action_payload["message"] = self._watch_action_message(
            watch=watch,
            watch_summary=watch_summary,
            action_instruction=action_instruction,
        )
        action_payload[WATCH_PAYLOAD_KEY] = action_watch
        return self._execute_agent_turn(job=job, run=run, payload=action_payload)

    def _execute_routine_turn(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        payload: dict[str, Any],
        watch: dict[str, Any],
        routine: RoutinePayloadV1,
    ) -> dict[str, Any]:
        """BRPR-06: typed routine pre/post-turn around the agent turn."""
        handler = self._routine_dispatcher.get(routine.routine_kind)
        if handler is None:
            return self._execute_watch_turn(
                job=job, run=run, payload=payload, watch=watch
            )

        routine_id = str(job.get("job_id", "") or "").strip() or "<unknown>"
        registry = getattr(self._runtime, "tools", None)
        if registry is None:
            return {
                "summary": "routine pre-turn aborted: tool registry unavailable",
                "error": True,
            }
        pre_turn_ctx = ToolRegistryPreTurnContext(
            registry=registry,
            routine_id=routine_id,
            session_id=str(payload.get("session_id") or "").strip(),
            agent_id=self._resolve_agent_id(job) or "",
        )
        try:
            facts = handler.pre_turn(
                routine=routine, routine_id=routine_id, ctx=pre_turn_ctx
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "summary": f"routine pre-turn failed: {exc}",
                "error": True,
            }

        routine_payload = dict(payload)
        routine_payload["message"] = self._routine_turn_message(
            check_instruction=str(watch.get("check_instruction", "")).strip(),
            facts_json=facts.model_dump_json(),
        )

        turn_result = self._execute_agent_turn(
            job=job, run=run, payload=routine_payload
        )
        if turn_result.get("error"):
            return turn_result

        sink = CronRunRoutineSink()
        try:
            post = handler.post_turn(
                routine=routine,
                routine_id=routine_id,
                facts=facts,
                outcome_text=str(turn_result.get("summary", "") or ""),
                sink=sink,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "summary": f"routine post-turn failed: {exc}",
                "error": True,
            }

        if post.updated_routine is not None:
            self._persist_routine_cursor(
                job=job,
                payload=payload,
                watch=watch,
                updated_routine=post.updated_routine,
            )

        summary_parts: list[str] = [f"routine={routine.routine_kind}"]
        if not post.ok:
            summary_parts.append(f"error_code={post.reason_code or 'unknown'}")
        elif sink.write_count == 0:
            summary_parts.append("no-op")
        else:
            summary_parts.append(f"artifact={sink.artifact_id}")
            summary_parts.append(f"new_findings={post.new_findings_count}")
            if sink.announce_summary:
                summary_parts.append(f"announce={sink.announce_summary}")
        return {
            "summary": " | ".join(summary_parts),
            "isolated_session_id": turn_result.get("isolated_session_id"),
            "metadata": {
                "routine_kind": routine.routine_kind,
                "routine_ok": post.ok,
                "routine_reason_code": post.reason_code or "",
                "routine_artifact_id": sink.artifact_id or "",
                "routine_kept_count": post.kept_count,
                "routine_dropped_count": post.dropped_count,
                "routine_new_findings_count": post.new_findings_count,
                "routine_announced": sink.announce_count > 0,
            },
        }

    def _routine_turn_message(self, *, check_instruction: str, facts_json: str) -> str:
        """Compose the deterministic agent-turn prompt for a routine."""
        instruction = check_instruction or "Review the supplied PR facts."
        return (
            f"{instruction}\n\n"
            "PR facts (typed, read-only):\n"
            f"{facts_json}\n\n"
            "Emit exactly one trailer block of the form:\n"
            "<routine_outcome>{...}</routine_outcome>\n"
            "where the JSON conforms to ReviewOutcomePayloadV1: "
            '{ "reviewed_prs": [ ... ], "skipped_prs": [ ... ] }. '
            "For each reviewed PR, set head_sha_reviewed to the head_sha "
            "supplied in the facts. The runtime drops entries whose "
            "head_sha_reviewed does not match. Free prose outside the "
            "trailer is recorded but not actionable."
        )

    def _persist_routine_cursor(
        self,
        *,
        job: dict[str, Any],
        payload: dict[str, Any],
        watch: dict[str, Any],
        updated_routine: RoutinePayloadV1,
    ) -> None:
        replacer = getattr(self._cron_store, "replace_cron_job_payload", None)
        if not callable(replacer):
            return
        updated_watch = dict(watch)
        updated_watch["routine"] = updated_routine.model_dump(mode="json")
        updated_payload = dict(payload)
        updated_payload[WATCH_PAYLOAD_KEY] = updated_watch
        try:
            replacer(job.get("job_id"), updated_payload)
        except Exception as exc:  # noqa: BLE001
            _CRON_LOGGER.warning(
                format_structured_event(
                    "cron.routine.persist_failed",
                    job_id=str(job.get("job_id", "") or ""),
                    error=str(exc),
                )
            )

    def _execute_agent_turn(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = getattr(self._runtime, "runtime_manager", None)
        if manager is None:
            return {"summary": "no runtime manager", "error": True}
        message = payload.get("message", "Run scheduled job.")
        agent_id = self._resolve_agent_id(job)
        if agent_id is None:
            return {"summary": "cron job missing agent id", "error": True}
        if not self._agent_is_registered(agent_id):
            return {
                "summary": f"cron job agent is not registered in this daemon: {agent_id}",
                "error": True,
            }

        request_payload = self._request_payload(
            job=job,
            run=run,
            message=message,
            payload=payload,
        )
        goal_runtime = self._resolve_goal_runtime(agent_id=agent_id)
        if goal_runtime is not None:
            try:
                goal_runtime.advance_from_cron(
                    goal_id=str(payload.get("goal_id", "") or "").strip() or None,
                    mission_id=str(payload.get("mission_id", "") or "").strip() or None,
                    session_api=self._resolve_session_api(agent_id=agent_id),
                    session_id=str(request_payload.get("session_id", "") or "").strip(),
                )
                request_meta = request_payload.setdefault("meta", {})
                if isinstance(request_meta, dict):
                    request_meta["goal_context_preloaded"] = "true"
            except Exception:
                pass
        cron_job_id = str(job.get("job_id", "") or "").strip()
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            request = self._request_builder(request_payload, agent_id)
            handle = manager.submit_turn(request)
            try:
                result = handle.result(timeout_s=self._timeout_s)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _CRON_LOGGER.warning(
                    format_structured_event(
                        "cron.turn.attempt_failed",
                        attempt=attempt,
                        max_attempts=self._max_attempts,
                        job_id=cron_job_id,
                        error=str(exc),
                    )
                )
                if attempt < self._max_attempts:
                    continue
                return {
                    "summary": f"Agent turn failed after {self._max_attempts} attempt(s): {last_error}",
                    "error": True,
                }
            summary = (
                str(getattr(result, "final_text", "") or "").strip()
                or "Agent turn completed."
            )
            metadata = getattr(result, "metadata", {}) or {}
            return {
                "summary": summary,
                "isolated_session_id": request.session_id,
                "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            }

        return {
            "summary": f"Agent turn exhausted {self._max_attempts} attempts",
            "error": True,
        }

    def _execute_idle_tick_turn(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """PAE-02: fire a proactive idle tick on an existing session."""
        manager = getattr(self._runtime, "runtime_manager", None)
        if manager is None:
            return {"summary": "no runtime manager", "error": True}

        agent_id = self._resolve_agent_id(job)
        if agent_id is None:
            return {"summary": "pae idle tick missing agent id", "error": True}
        if not self._agent_is_registered(agent_id):
            return {
                "summary": (
                    f"pae idle tick agent is not registered in this daemon: {agent_id}"
                ),
                "error": True,
            }

        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return {
                "summary": "pae idle tick payload missing session_id",
                "error": True,
            }
        plan_id = str(payload.get("plan_id") or "").strip()

        try:
            payload_grace = int(payload.get("user_activity_grace_seconds", 0) or 0)
        except (TypeError, ValueError):
            payload_grace = 0
        suppressed = self._check_idle_tick_user_activity_gate(
            agent_id=agent_id,
            session_id=session_id,
            plan_id=plan_id,
            grace_seconds=payload_grace,
        )
        if suppressed is not None:
            return suppressed

        request_payload = self._request_payload(
            job=job,
            run=run,
            message="",  # idle ticks don't carry a user message
            payload=payload,
        )
        request_payload["session_id"] = session_id
        cron_meta = request_payload.setdefault("cron", {})
        if isinstance(cron_meta, dict):
            cron_meta["pae_idle_tick"] = "true"
            if plan_id:
                cron_meta["pae_plan_id"] = plan_id
        request_meta = request_payload.setdefault("meta", {})
        if isinstance(request_meta, dict):
            request_meta["pae_idle_tick"] = "true"
            if plan_id:
                request_meta["pae_plan_id"] = plan_id

        cron_job_id = str(job.get("job_id", "") or "").strip()
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            request = self._request_builder(request_payload, agent_id)
            handle = manager.submit_turn(request)
            try:
                result = handle.result(timeout_s=self._timeout_s)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _CRON_LOGGER.warning(
                    format_structured_event(
                        "pae.idle_tick.attempt_failed",
                        attempt=attempt,
                        max_attempts=self._max_attempts,
                        job_id=cron_job_id,
                        session_id=session_id,
                        error=str(exc),
                    )
                )
                if attempt < self._max_attempts:
                    continue
                return {
                    "summary": (
                        f"PAE idle tick failed after {self._max_attempts} "
                        f"attempt(s): {last_error}"
                    ),
                    "error": True,
                }
            metadata = getattr(result, "metadata", {}) or {}
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            final_text = str(getattr(result, "final_text", "") or "").strip()
            if (
                str(metadata_dict.get("pae_idle_tick_noop", "")).strip().lower()
                == "true"
            ):
                summary = "PAE idle tick: no-op"
            elif final_text:
                summary = final_text
            else:
                summary = "PAE idle tick completed."
            return {
                "summary": summary,
                "metadata": metadata_dict,
            }

        return {
            "summary": f"PAE idle tick exhausted {self._max_attempts} attempts",
            "error": True,
        }

    def _check_idle_tick_user_activity_gate(
        self,
        *,
        agent_id: str,
        session_id: str,
        plan_id: str,
        grace_seconds: int,
    ) -> dict[str, Any] | None:
        if grace_seconds <= 0:
            return None
        session_api = self._resolve_session_api(agent_id=agent_id)
        if session_api is None:
            return None
        try:
            from openminion.modules.brain.loop.proactive_entrypoint import (
                is_user_active,
            )
        except Exception:  # noqa: BLE001
            return None
        if not is_user_active(
            session_api=session_api,
            session_id=session_id,
            grace_seconds=grace_seconds,
        ):
            return None
        try:
            append_event = getattr(session_api, "append_event", None)
            if callable(append_event):
                append_event(
                    session_id,
                    "pae.idle_tick.suppressed",
                    {
                        "reason": "user_activity",
                        "grace_seconds": grace_seconds,
                        "plan_id": plan_id,
                    },
                    actor_type="system",
                    actor_id=agent_id,
                    importance=2,
                    redaction="none",
                    status="ok",
                )
        except Exception:  # noqa: BLE001 — telemetry is best-effort
            pass
        return {
            "summary": (
                f"PAE idle tick suppressed: user activity within {grace_seconds}s"
            ),
            "metadata": {"pae_suppressed": "user_activity"},
        }

    def _resolve_session_api(self, *, agent_id: str) -> Any | None:
        del agent_id  # reserved for future per-agent resolution
        cron_store = getattr(self, "_cron_store", None)
        if (
            cron_store is not None
            and callable(getattr(cron_store, "list_events", None))
            and callable(getattr(cron_store, "append_event", None))
        ):
            return cron_store
        runtime_manager = getattr(self._runtime, "runtime_manager", None)
        if runtime_manager is None:
            return None
        for attr in ("get_session_api", "session_api"):
            resolver = getattr(runtime_manager, attr, None)
            if callable(resolver):
                try:
                    return resolver()
                except Exception:  # noqa: BLE001
                    return None
            if resolver is not None:
                return resolver
        return None

    def _resolve_agent_id(self, job: dict[str, Any]) -> str | None:
        requested_agent_id = str(job.get("agent_id", "") or "").strip()
        if requested_agent_id:
            return requested_agent_id
        try:
            return resolve_default_agent_id(self._runtime.config)
        except Exception:  # noqa: BLE001
            return None

    def _resolve_goal_runtime(self, *, agent_id: str) -> Any | None:
        resolver = getattr(self._runtime, "resolve_agent_service", None)
        if not callable(resolver):
            return None
        try:
            agent_service = resolver(agent_id)
        except Exception:  # noqa: BLE001
            return None
        runner_getter = getattr(agent_service, "_get_runner", None)
        if callable(runner_getter):
            try:
                runner = runner_getter()
            except Exception:  # noqa: BLE001
                return None
        else:
            runner = getattr(agent_service, "_runner", None)
        return getattr(runner, "goal_runtime", None) if runner is not None else None

    def _agent_is_registered(self, agent_id: str) -> bool:
        list_registered_agents = getattr(self._runtime, "list_registered_agents", None)
        if not callable(list_registered_agents):
            return True
        try:
            registered_agents = {
                str(item or "").strip()
                for item in list_registered_agents()
                if str(item or "").strip()
            }
        except Exception:  # noqa: BLE001
            registered_agents = set()
        return not registered_agents or agent_id in registered_agents

    def _request_payload(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        message: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_payload = (
            payload if isinstance(payload, dict) else job.get("payload", {})
        )
        payload_session_id = ""
        if isinstance(resolved_payload, dict):
            payload_session_id = str(
                resolved_payload.get("session_id", "") or ""
            ).strip()
        cron_job_id = str(job.get("job_id", "") or "").strip()
        cron_run_id = str(run.get("run_id", "") or "").strip()
        scheduled_for = str(run.get("due_at", "") or "").strip()
        if not scheduled_for:
            scheduled_for = str(job.get("next_due_at", "") or "").strip()
        isolated_session_id = str(run.get("isolated_session_id", "") or "").strip()

        request_payload: dict[str, Any] = {
            "message": message,
            "session_id": payload_session_id or isolated_session_id or "cron-session",
            "trace_id": run["run_id"],
        }
        cron_meta: dict[str, str] = {}
        if cron_job_id:
            cron_meta["cron_job_id"] = cron_job_id
        if cron_run_id:
            cron_meta["cron_run_id"] = cron_run_id
        if scheduled_for:
            cron_meta["scheduled_for"] = scheduled_for
        if isinstance(resolved_payload, dict):
            linked_task_id = str(
                resolved_payload.get("linked_task_id", "") or ""
            ).strip()
            if linked_task_id:
                cron_meta["linked_task_id"] = linked_task_id
            goal_id = str(resolved_payload.get("goal_id", "") or "").strip()
            if goal_id:
                cron_meta["goal_id"] = goal_id
            mission_id = str(resolved_payload.get("mission_id", "") or "").strip()
            if mission_id:
                cron_meta["mission_id"] = mission_id
            consolidation = self._consolidation_metadata(resolved_payload)
            if consolidation is not None:
                cron_meta["memory_consolidation_job"] = "true"
                cron_meta["memory_consolidation_target_scope"] = str(
                    consolidation.get("target_scope", "") or ""
                ).strip()
                cron_meta["memory_consolidation_batch_limit"] = str(
                    int(
                        consolidation.get(
                            "batch_limit",
                            DEFAULT_CONSOLIDATION_BATCH_LIMIT,
                        )
                        or DEFAULT_CONSOLIDATION_BATCH_LIMIT
                    )
                )
                cron_meta["memory_consolidation_max_iterations"] = str(
                    int(
                        consolidation.get(
                            "max_iterations",
                            DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
                        )
                        or DEFAULT_CONSOLIDATION_MAX_ITERATIONS
                    )
                )
                cron_meta["memory_consolidation_timeout_seconds"] = str(
                    int(
                        consolidation.get(
                            "timeout_seconds",
                            DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
                        )
                        or DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS
                    )
                )
            watch = self._watch_metadata(resolved_payload)
            if watch is not None:
                watch_turn_kind = (
                    str(
                        watch.get("turn_kind", WATCH_TURN_KIND_CHECK)
                        or WATCH_TURN_KIND_CHECK
                    ).strip()
                    or WATCH_TURN_KIND_CHECK
                )
                cron_meta["watch_job"] = "true"
                cron_meta["watch_turn_kind"] = watch_turn_kind
                cron_meta["watch_description"] = str(
                    watch.get("description", "") or ""
                ).strip()
                cron_meta["watch_alert_condition"] = str(
                    watch.get("alert_condition", "") or ""
                ).strip()
                if watch_turn_kind == WATCH_TURN_KIND_CHECK:
                    cron_meta["watch_allowed_tools"] = ",".join(
                        [
                            str(item).strip()
                            for item in list(
                                watch.get("allowed_tools", WATCH_DEFAULT_ALLOWED_TOOLS)
                                or WATCH_DEFAULT_ALLOWED_TOOLS
                            )
                            if str(item).strip()
                        ]
                    )
                if watch_turn_kind == WATCH_TURN_KIND_ACTION:
                    cron_meta["watch_write_authorized"] = str(
                        bool(watch.get("write_authorized", False))
                    ).lower()
                    cron_meta["watch_write_authorization_scope"] = "watch_job"
                cron_meta["watch_max_iterations"] = str(
                    int(
                        watch.get("max_iterations", DEFAULT_WATCH_MAX_ITERATIONS)
                        or DEFAULT_WATCH_MAX_ITERATIONS
                    )
                )
                cron_meta["watch_timeout_seconds"] = str(
                    int(
                        watch.get("timeout_seconds", DEFAULT_WATCH_TIMEOUT_SECONDS)
                        or DEFAULT_WATCH_TIMEOUT_SECONDS
                    )
                )
        if cron_meta:
            request_payload["meta"] = cron_meta
        if isinstance(resolved_payload, dict):
            timeout_seconds = int(resolved_payload.get("timeout_seconds", 0) or 0)
            if timeout_seconds <= 0:
                watch = self._watch_metadata(resolved_payload)
                timeout_seconds = int((watch or {}).get("timeout_seconds", 0) or 0)
            if timeout_seconds > 0:
                request_payload["timeout_seconds"] = timeout_seconds
        return request_payload

    def _consolidation_metadata(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        raw = payload.get(CONSOLIDATION_PAYLOAD_KEY)
        if not isinstance(raw, dict):
            return None
        metadata = dict(raw)
        metadata.setdefault("batch_limit", DEFAULT_CONSOLIDATION_BATCH_LIMIT)
        metadata.setdefault("max_iterations", DEFAULT_CONSOLIDATION_MAX_ITERATIONS)
        metadata.setdefault("timeout_seconds", DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS)
        return metadata

    def _watch_metadata(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        raw = payload.get(WATCH_PAYLOAD_KEY)
        if not isinstance(raw, dict):
            return None
        watch = dict(raw)
        watch.setdefault("max_checks", DEFAULT_WATCH_MAX_CHECKS)
        watch.setdefault("checks_completed", 0)
        watch.setdefault("ttl_minutes", DEFAULT_WATCH_TTL_MINUTES)
        watch.setdefault("timeout_seconds", DEFAULT_WATCH_TIMEOUT_SECONDS)
        watch.setdefault("max_iterations", DEFAULT_WATCH_MAX_ITERATIONS)
        watch.setdefault("allowed_tools", list(WATCH_DEFAULT_ALLOWED_TOOLS))
        watch.setdefault("turn_kind", WATCH_TURN_KIND_CHECK)
        watch.setdefault("write_authorized", False)
        watch.setdefault("write_audit", [])
        return watch

    def _watch_ttl_expired(self, *, job: dict[str, Any], watch: dict[str, Any]) -> bool:
        created_at = str(watch.get("created_at") or job.get("created_at") or "").strip()
        if not created_at:
            return False
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        ttl_minutes = int(
            watch.get("ttl_minutes", DEFAULT_WATCH_TTL_MINUTES)
            or DEFAULT_WATCH_TTL_MINUTES
        )
        return created + timedelta(minutes=ttl_minutes) <= now

    def _persist_watch_progress(
        self,
        *,
        job: dict[str, Any],
        payload: dict[str, Any],
        watch: dict[str, Any],
        checks_completed: int,
        summary: str,
        condition_met: bool,
        terminal_reason: str,
        write_audit_entries: tuple[dict[str, Any], ...] = (),
    ) -> None:
        replacer = getattr(self._cron_store, "replace_cron_job_payload", None)
        if not callable(replacer):
            return
        updated_watch = dict(watch)
        updated_watch["checks_completed"] = checks_completed
        updated_watch["last_check_at"] = datetime.now(timezone.utc).isoformat()
        updated_watch["last_check_summary"] = summary
        updated_watch["last_condition_met"] = bool(condition_met)
        updated_watch["last_terminal_reason"] = terminal_reason
        if write_audit_entries:
            existing_audit = [
                item
                for item in list(updated_watch.get("write_audit", []) or [])
                if isinstance(item, dict)
            ]
            updated_watch["write_audit"] = [*existing_audit, *write_audit_entries]
        updated_payload = dict(payload)
        updated_payload[WATCH_PAYLOAD_KEY] = updated_watch
        try:
            replacer(str(job.get("job_id", "") or "").strip(), updated_payload)
        except Exception:
            return

    def _watch_terminal_summary(
        self,
        *,
        watch: dict[str, Any],
        checks_completed: int,
        terminal_reason: str,
        fallback: str,
    ) -> str:
        if terminal_reason == "ttl_expired":
            description = str(watch.get("description", "") or "").strip()
            if description:
                return f"Watch expired: {description}"
            return fallback or "Watch expired."
        if terminal_reason == "max_checks_reached":
            description = str(watch.get("description", "") or "").strip()
            if description:
                return (
                    f"Watch expired after {checks_completed} checks without alert: "
                    f"{description}"
                )
            return fallback or "Watch expired after reaching max checks."
        return fallback

    def _watch_output(
        self,
        *,
        condition_met: bool,
        terminal: bool,
        deliver: bool,
        checks_completed: int,
        terminal_reason: str,
        summary: str,
        action_executed: bool = False,
        action_summary: str = "",
    ) -> dict[str, Any]:
        return {
            "watch_delivery_requested": bool(deliver),
            "watch_terminal": bool(terminal),
            "watch_condition_met": bool(condition_met),
            "watch_checks_completed": int(checks_completed),
            "watch_terminal_reason": str(terminal_reason or ""),
            "watch_summary": str(summary or ""),
            "watch_action_executed": bool(action_executed),
            "watch_action_summary": str(action_summary or ""),
        }

    def _watch_action_message(
        self,
        *,
        watch: dict[str, Any],
        watch_summary: str,
        action_instruction: str,
    ) -> str:
        description = str(watch.get("description", "") or "").strip()
        alert_condition = str(watch.get("alert_condition", "") or "").strip()
        lines = [
            "This is a watch-triggered follow-up action turn.",
            f"Declared action: {action_instruction}",
        ]
        if description:
            lines.append(f"Watch description: {description}")
        if alert_condition:
            lines.append(f"Alert condition: {alert_condition}")
        if watch_summary:
            lines.append(f"Triggered check summary: {watch_summary}")
        if bool(watch.get("write_authorized", False)):
            lines.append(
                "This watch has operator-authorized background write access for this "
                "watch job only. Write-capable tools may run without an interactive "
                "confirmation prompt; normal tool policy/path checks still apply."
            )
        else:
            lines.append(
                "Use auto-allowed/background-safe tools only. "
                "Confirmation-required tools fail closed in watch-triggered action runs."
            )
        return "\n".join(lines)

    def _execute_system_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        days = int(payload.get("kwargs", {}).get("days", 7))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            deleted = self._cron_store.delete_old_cron_runs(cutoff)
            summary = f"Pruned {deleted} old cron runs."
            _CRON_LOGGER.info(
                format_structured_event(
                    "cron.cleanup.completed",
                    summary=summary,
                    deleted=deleted,
                )
            )
            return {"summary": summary, "status": "completed"}
        except Exception as exc:  # noqa: BLE001
            return {"summary": f"Failed to prune cron runs: {exc}", "error": True}
