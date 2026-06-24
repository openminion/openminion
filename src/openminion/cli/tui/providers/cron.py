from __future__ import annotations

from datetime import datetime
from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


class RuntimeCronProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, cron_repository: Any | None, *, job_limit: int = 50) -> None:
        self._cron_repository = cron_repository
        self._job_limit = max(1, int(job_limit))

    def list_jobs(self) -> list[dict[str, Any]]:
        if self._cron_repository is None:
            return []
        list_cron_jobs = getattr(self._cron_repository, "list_cron_jobs", None)
        list_cron_runs = getattr(self._cron_repository, "list_cron_runs", None)
        if not callable(list_cron_jobs):
            return []

        try:
            jobs = list_cron_jobs(limit=self._job_limit)
        except Exception:
            return []
        if not isinstance(jobs, list):
            return []

        result: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            if not job_id:
                continue
            runs: list[dict[str, Any]] = []
            if callable(list_cron_runs):
                try:
                    raw_runs = list_cron_runs(job_id=job_id, limit=5)
                    if isinstance(raw_runs, list):
                        runs = [
                            self._map_run(item)
                            for item in raw_runs
                            if isinstance(item, dict)
                        ]
                except Exception:
                    runs = []

            result.append(
                {
                    "id": job_id,
                    "expr": self._schedule_to_expr(job),
                    "next_due": str(
                        job.get("next_due_at") or job.get("next_due") or "—"
                    ),
                    "enabled": bool(job.get("enabled", True)),
                    "misfire_policy": self._misfire_policy(job),
                    "recent_runs": runs,
                }
            )

        return result

    def list_recent_runs(self, job_id: str, limit: int = 10) -> list[dict[str, Any]]:
        if self._cron_repository is None:
            return []
        list_cron_runs = getattr(self._cron_repository, "list_cron_runs", None)
        if not callable(list_cron_runs):
            return []

        safe_limit = max(1, int(limit))
        try:
            runs = list_cron_runs(job_id=str(job_id or "").strip(), limit=safe_limit)
        except Exception:
            return []
        if not isinstance(runs, list):
            return []
        return [self._map_run(run) for run in runs if isinstance(run, dict)]

    def toggle_job_enabled(self, job_id: str, enabled: bool) -> bool:
        if self._cron_repository is None:
            return False

        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        for candidate in (
            getattr(self._cron_repository, "set_cron_job_enabled", None),
            getattr(
                getattr(self._cron_repository, "_store", None),
                "set_cron_job_enabled",
                None,
            ),
        ):
            if not callable(candidate):
                continue
            try:
                candidate(normalized_job_id, bool(enabled))
            except Exception:
                continue
            return True
        return False

    @staticmethod
    def _misfire_policy(job: dict[str, Any]) -> str:
        raw = job.get("misfire_policy")
        if isinstance(raw, dict):
            return str(raw.get("kind") or "skip")
        return str(raw or "skip")

    @staticmethod
    def _schedule_to_expr(job: dict[str, Any]) -> str:
        schedule = job.get("schedule") or job.get("schedule_json")
        if isinstance(schedule, dict):
            kind = str(schedule.get("kind") or "").strip().lower()
            if "expr" in schedule and str(schedule.get("expr") or "").strip():
                return str(schedule.get("expr"))
            if kind == "interval" and "every_ms" in schedule:
                return f"every {int(schedule['every_ms'])}ms"
            if kind == "at" and str(schedule.get("at") or "").strip():
                return str(schedule.get("at"))
            if kind:
                return kind
        if schedule is not None:
            return str(schedule)
        return ""

    @staticmethod
    def _map_run(run: dict[str, Any]) -> dict[str, Any]:
        raw_state = str(run.get("state") or "").strip().lower()
        state_map = {
            "finished": "success",
            "failed": "failed",
            "timed_out": "timeout",
            "running": "running",
            "queued": "queued",
        }
        state = state_map.get(raw_state, raw_state or "unknown")
        at = str(
            run.get("due_at") or run.get("started_at") or run.get("created_at") or ""
        )
        duration = RuntimeCronProvider._duration(run)
        return {
            "state": state,
            "at": at[:19],
            "duration": duration,
        }

    @staticmethod
    def _duration(run: dict[str, Any]) -> str:
        started = str(run.get("started_at") or "").strip()
        finished = str(run.get("finished_at") or "").strip()
        if not started or not finished:
            return ""
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            finish_dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        except ValueError:
            return ""
        seconds = max(0, int((finish_dt - start_dt).total_seconds()))
        return f"{seconds}s"
