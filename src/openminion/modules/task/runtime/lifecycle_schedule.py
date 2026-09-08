# mypy: ignore-errors
from __future__ import annotations

from typing import Any
from collections.abc import Mapping

from openminion.modules.task.constants import (
    TASK_INTERNAL_PAUSE_REASON_KEY,
    TASK_INTERNAL_PAUSE_SOURCE_KEY,
    TASK_INTERNAL_SCHEDULE_KEY,
)
from openminion.modules.task.scheduling.coordination import ExpiredOneShotTaskError
from openminion.modules.task.scheduling.schedule import parse_iso_datetime, utc_now

from .lifecycle_models import (
    TaskLifecycleRecord,
    TaskLifecycleState,
    bounded_task_run_error,
)


class TaskManagerScheduleMixin:
    _TERMINAL_RUN_STATES = {"finished", "failed", "cancelled", "timed_out"}

    def schedule_task(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        concurrency_key: str | None = None,
        max_attempts: int = 3,
        retry_backoff_s: int = 30,
        job_id: str | None = None,
    ) -> TaskLifecycleRecord:
        task_payload = dict(payload)
        task_payload[TASK_INTERNAL_SCHEDULE_KEY] = True
        created_job_id = self._cron_repository.add_cron_job(
            name=name,
            schedule=schedule,
            payload=task_payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            # Task lifecycle records need the cron job and run history for reconciliation.
            delete_after_run=False,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            concurrency_key=concurrency_key,
            max_attempts=max_attempts,
            retry_backoff_s=retry_backoff_s,
            job_id=job_id,
        )
        try:
            return self.ensure_task_record(
                cron_job_id=created_job_id,
                agent_id=agent_id,
                task_id=created_job_id,
            )
        except Exception:
            try:
                self._cron_repository.delete_cron_job(created_job_id)
            except Exception:
                pass
            raise

    def schedule_user_task(
        self,
        *,
        name: str,
        instruction: str,
        schedule: Mapping[str, Any],
        agent_id: str,
        origin: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": "agentTurn",
            "message": str(instruction).strip(),
        }
        if origin:
            payload["_openminion_origin"] = dict(origin)
        payload[TASK_INTERNAL_SCHEDULE_KEY] = True
        for job in self.list_scheduled_jobs(limit=1000):
            if not bool(job.get("enabled")):
                continue
            if str(job.get("name") or "").strip() != str(name).strip():
                continue
            if str(job.get("agent_id") or "").strip() != str(agent_id).strip():
                continue
            if dict(job.get("schedule") or {}) != dict(schedule):
                continue
            stored_payload = dict(job.get("payload") or {})
            comparable_stored = dict(stored_payload)
            comparable_stored.pop(TASK_INTERNAL_SCHEDULE_KEY, None)
            comparable_expected = dict(payload)
            comparable_expected.pop(TASK_INTERNAL_SCHEDULE_KEY, None)
            if comparable_stored != comparable_expected:
                continue
            if not bool(stored_payload.get(TASK_INTERNAL_SCHEDULE_KEY)):
                stored_payload[TASK_INTERNAL_SCHEDULE_KEY] = True
                self.replace_cron_job_payload(str(job["job_id"]), stored_payload)
                job = {**job, "payload": stored_payload}
            self.ensure_task_record_for_job(job)
            return {
                "record": self.get_task_by_job(str(job["job_id"])),
                "job": job,
                "deduped": True,
            }

        record = self.schedule_task(
            name=name,
            schedule=schedule,
            payload=payload,
            agent_id=agent_id,
            session_target="isolated",
            misfire_policy="skip",
        )
        return {
            "record": record,
            "job": self.get_scheduled_job(record.cron_job_id) or {},
            "deduped": False,
        }

    def get_scheduled_job(self, task_id: str) -> dict[str, Any] | None:
        return self._cron_repository.get_cron_job(task_id)

    def list_scheduled_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        return self._cron_repository.list_cron_jobs(limit=limit)

    def _require_scheduled_record(self, task_id: str) -> TaskLifecycleRecord:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        if record is not None:
            return record
        job = self.get_scheduled_job(task_id)
        if job is None:
            raise KeyError(f"task not found: {task_id}")
        return self.ensure_task_record_for_job(job)

    def set_scheduled_job_enabled(
        self, task_id: str, *, enabled: bool
    ) -> dict[str, Any]:
        record = self._require_scheduled_record(task_id)
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, enabled)
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return refreshed

    def list_scheduled_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._cron_repository.list_cron_runs(
            job_id=job_id,
            limit=limit,
            states=states,
        )

    def create_cron_job(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        delete_after_run: bool | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        concurrency_key: str | None = None,
        max_attempts: int = 3,
        retry_backoff_s: int = 30,
        job_id: str | None = None,
    ) -> str:
        return self._cron_repository.add_cron_job(
            name=name,
            schedule=schedule,
            payload=payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            delete_after_run=delete_after_run,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            concurrency_key=concurrency_key,
            max_attempts=max_attempts,
            retry_backoff_s=retry_backoff_s,
            job_id=job_id,
        )

    def replace_cron_job_payload(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        replacer = getattr(self._cron_repository, "replace_cron_job_payload", None)
        if not callable(replacer):
            raise NotImplementedError("Cron payload replacement is unavailable")
        replacer(job_id, payload)

    def delete_scheduled_job(self, job_id: str) -> None:
        normalized = str(job_id or "").strip()
        if not normalized:
            return
        try:
            self._cron_repository.delete_cron_job(normalized)
        except Exception:
            pass

    def get_linked_cron_job(self, task_id: str) -> str | None:
        record = self.get_task(task_id)
        if record is None:
            return None
        linked = str(record.metadata.get("linked_cron_job_id") or "").strip()
        return linked or None

    def find_task_by_linked_cron_job(
        self, cron_job_id: str
    ) -> TaskLifecycleRecord | None:
        normalized = str(cron_job_id or "").strip()
        if not normalized:
            return None
        for record in self._lifecycle_repository.list(limit=1000):
            linked = str(record.metadata.get("linked_cron_job_id") or "").strip()
            if linked == normalized:
                return record
        return None

    def record_scheduled_outcome(
        self,
        *,
        cron_job_id: str,
        run: Mapping[str, Any],
    ) -> TaskLifecycleRecord | None:
        record = self.get_task_by_job(cron_job_id)
        if record is None:
            return None
        run_state = str(run.get("state") or "").strip()
        if run_state not in self._TERMINAL_RUN_STATES:
            return record

        if run_state == "cancelled" and int(run.get("attempts") or 0) == 0:
            return record

        job = self.get_scheduled_job(record.cron_job_id)
        schedule = dict((job or {}).get("schedule") or {})
        one_time = str(schedule.get("kind") or "").strip() == "at"

        run_id = str(run.get("run_id") or "").strip()
        prior_run = record.metadata.get("last_run")
        if (
            isinstance(prior_run, Mapping)
            and prior_run.get("run_id") == run_id
            and (
                not one_time
                or record.state
                in {
                    TaskLifecycleState.CANCELLED,
                    TaskLifecycleState.DONE,
                    TaskLifecycleState.FAILED,
                }
            )
        ):
            return record
        error = run.get("error")
        normalized_error = bounded_task_run_error(error)
        metadata = dict(record.metadata)
        metadata["last_run"] = {
            "run_id": run_id,
            "state": run_state,
            "due_at": run.get("due_at"),
            "finished_at": run.get("finished_at"),
            "summary": str(run.get("summary") or "")[:1000] or None,
            "last_error": normalized_error,
        }
        if one_time and job is not None and bool(job.get("enabled")):
            self._cron_repository.set_cron_job_enabled(record.cron_job_id, False)
        if record.state == TaskLifecycleState.CANCELLED:
            return self._lifecycle_repository.record_scheduled_outcome(
                task_id=record.task_id,
                expected_state=record.state,
                to_state=record.state,
                metadata=metadata,
            )

        target = record.state
        reason = None
        if one_time and record.state in {
            TaskLifecycleState.PAUSED,
            TaskLifecycleState.ACTIVE,
        }:
            target = (
                TaskLifecycleState.DONE
                if run_state == "finished"
                else TaskLifecycleState.FAILED
            )
            reason = (
                str((normalized_error or {}).get("code") or run_state)
                if target == TaskLifecycleState.FAILED
                else None
            )
        return self._lifecycle_repository.record_scheduled_outcome(
            task_id=record.task_id,
            expected_state=record.state,
            to_state=target,
            metadata=metadata,
            failure_reason=reason,
        )

    def reconcile_scheduled_outcomes(
        self,
        cron_job_id: str | None = None,
        *,
        limit: int = 100,
    ) -> int:
        normalized_job_id = str(cron_job_id or "").strip()
        if normalized_job_id:
            records = [self.get_task_by_job(normalized_job_id)]
        else:
            page_limit = max(100, min(limit * 10, 1000))
            records = self._lifecycle_repository.list_reconciliation_candidates(
                limit=page_limit,
                after=self._reconciliation_cursor,
            )
        reconciled = 0
        last_examined = None
        for record in records:
            last_examined = record
            if record is None or record.state in {
                TaskLifecycleState.DONE,
                TaskLifecycleState.FAILED,
            }:
                continue
            runs = self.list_scheduled_runs(job_id=record.cron_job_id, limit=1)
            if (
                not runs
                or str(runs[0].get("state") or "") not in self._TERMINAL_RUN_STATES
                or (
                    str(runs[0].get("state") or "") == "cancelled"
                    and int(runs[0].get("attempts") or 0) == 0
                )
            ):
                continue
            prior_run = record.metadata.get("last_run")
            if (
                record.state == TaskLifecycleState.CANCELLED
                and isinstance(prior_run, Mapping)
                and prior_run.get("run_id") == runs[0].get("run_id")
            ):
                continue
            self.record_scheduled_outcome(
                cron_job_id=record.cron_job_id,
                run=runs[0],
            )
            reconciled += 1
            if reconciled >= max(1, min(limit, 1000)):
                break
        if not normalized_job_id:
            if last_examined is not None and (
                reconciled >= max(1, min(limit, 1000)) or len(records) == page_limit
            ):
                self._reconciliation_cursor = (
                    last_examined.created_at,
                    last_examined.task_id,
                )
            else:
                self._reconciliation_cursor = None
        return reconciled

    def cancel_task(self, task_id: str) -> TaskLifecycleRecord:
        record = self._require_scheduled_record(task_id)
        if record.state == TaskLifecycleState.CANCELLED:
            return record
        if record.state not in {
            TaskLifecycleState.ACTIVE,
            TaskLifecycleState.PAUSED,
        }:
            raise ValueError(
                f"invalid task state transition: {record.state.value} -> cancelled"
            )
        job = self.get_scheduled_job(record.cron_job_id)
        if job is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(
            record.cron_job_id,
            False,
            cancel_queued=False,
        )
        cancelled = self._lifecycle_repository.cancel_scheduled_task(
            task_id=record.task_id,
            expected_state=record.state,
        )
        self._cron_repository.set_cron_job_enabled(
            record.cron_job_id,
            False,
            cancel_queued=True,
        )
        return cancelled

    def pause_task(self, task_id: str) -> tuple[TaskLifecycleRecord, dict[str, Any]]:
        record = self._require_scheduled_record(task_id)
        job = self.get_scheduled_job(record.cron_job_id)
        if job is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(
            record.cron_job_id,
            False,
            cancel_queued=False,
        )
        paused = self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
        self._cron_repository.set_cron_job_enabled(
            record.cron_job_id,
            False,
            cancel_queued=True,
        )
        refreshed = self.get_scheduled_job(paused.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return paused, refreshed

    def resume_task(self, task_id: str) -> tuple[TaskLifecycleRecord, dict[str, Any]]:
        record = self._require_scheduled_record(task_id)
        job = self.get_scheduled_job(record.cron_job_id)
        if job is None:
            raise KeyError(f"task not found: {task_id}")
        schedule = dict(job.get("schedule") or {})
        at_value = str(schedule.get("at") or "").strip()
        if (
            str(schedule.get("kind") or "").strip() == "at"
            and at_value
            and parse_iso_datetime(at_value) <= utc_now()
        ):
            raise ExpiredOneShotTaskError(
                "one-shot task is already expired and cannot be resumed"
            )
        if record.state != TaskLifecycleState.PAUSED:
            raise ValueError(
                f"invalid task state transition: {record.state.value} -> active"
            )
        self._cron_repository.set_cron_job_enabled(
            record.cron_job_id,
            False,
            cancel_queued=True,
        )
        original_payload = dict(job.get("payload") or {})
        payload = dict(original_payload)
        pause_metadata_present = (
            TASK_INTERNAL_PAUSE_REASON_KEY in payload
            or TASK_INTERNAL_PAUSE_SOURCE_KEY in payload
        )
        resumed = None
        payload_cleaned = False
        enabled = False
        try:
            if pause_metadata_present:
                payload.pop(TASK_INTERNAL_PAUSE_REASON_KEY, None)
                payload.pop(TASK_INTERNAL_PAUSE_SOURCE_KEY, None)
                self.replace_cron_job_payload(record.cron_job_id, payload)
                payload_cleaned = True
            self._cron_repository.set_cron_job_enabled(record.cron_job_id, True)
            enabled = True
            refreshed = self.get_scheduled_job(record.cron_job_id)
            if refreshed is None:
                raise KeyError(f"task not found: {task_id}")
            resumed = self.transition_task(
                task_id=record.task_id,
                to_state=TaskLifecycleState.ACTIVE,
            )
        finally:
            if resumed is None:
                try:
                    if enabled:
                        self._cron_repository.set_cron_job_enabled(
                            record.cron_job_id,
                            False,
                            cancel_queued=True,
                        )
                finally:
                    if payload_cleaned:
                        self.replace_cron_job_payload(
                            record.cron_job_id,
                            original_payload,
                        )
        return resumed, refreshed

    def _cleanup_linked_cron_job(self, *, task_id: str) -> None:
        record = self.get_task(task_id)
        if record is None:
            return
        metadata = dict(record.metadata)
        linked_cron_job_id = str(metadata.get("linked_cron_job_id") or "").strip()
        if not linked_cron_job_id:
            return
        try:
            self.delete_scheduled_job(linked_cron_job_id)
        finally:
            metadata.pop("linked_cron_job_id", None)
            self.update_task_metadata(task_id=task_id, metadata=metadata)
