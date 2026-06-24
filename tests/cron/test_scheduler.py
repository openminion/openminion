from __future__ import annotations

import threading
from time import sleep
from uuid import uuid4

from openminion.services.cron import CronScheduler


class FakeCronStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self._due_job_ids: list[str] = []
        self.renew_counts: dict[str, int] = {}
        self.finished = threading.Event()
        self.finished_count = 0
        self.delivery_targets: dict[str, set[str]] = {}
        self.deleted_job_ids: list[str] = []

    def add_job(
        self, *, job_id: str, payload: dict, delivery: dict | None = None
    ) -> None:
        with self._lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "payload": dict(payload),
                "delivery": dict(delivery or {"mode": "none"}),
            }

    def seed_due(self, job_id: str) -> None:
        with self._lock:
            self._due_job_ids.append(job_id)

    def enqueue_due_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        max_jobs: int = 50,
        now_iso: str | None = None,
    ) -> list[dict]:
        del lease_ttl_s, now_iso
        queued: list[dict] = []
        with self._lock:
            for _ in range(min(max_jobs, len(self._due_job_ids))):
                job_id = self._due_job_ids.pop(0)
                run_id = uuid4().hex
                payload = {
                    "run_id": run_id,
                    "job_id": job_id,
                    "state": "queued",
                    "attempts": 0,
                    "lease_owner": daemon_id,
                }
                self.runs[run_id] = payload
                queued.append(dict(payload))
        return queued

    def acquire_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        limit: int = 10,
        now_iso: str | None = None,
    ) -> list[dict]:
        del lease_ttl_s, now_iso
        acquired: list[dict] = []
        with self._lock:
            for run in self.runs.values():
                if len(acquired) >= limit:
                    break
                if run["state"] != "queued":
                    continue
                run["state"] = "running"
                run["attempts"] = int(run.get("attempts", 0)) + 1
                run["lease_owner"] = daemon_id
                acquired.append(dict(run))
        return acquired

    def renew_cron_run_lease(
        self,
        run_id: str,
        *,
        daemon_id: str,
        lease_ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool:
        del lease_ttl_s, now_iso
        with self._lock:
            run = self.runs.get(run_id)
            if (
                run is None
                or run.get("state") != "running"
                or run.get("lease_owner") != daemon_id
            ):
                return False
            self.renew_counts[run_id] = self.renew_counts.get(run_id, 0) + 1
            return True

    def get_cron_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return dict(job) if job is not None else None

    def finish_cron_run(
        self,
        run_id: str,
        *,
        state: str,
        summary: str | None = None,
        artifact_refs: list[dict] | None = None,
        error: dict | None = None,
        isolated_session_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict | None:
        del artifact_refs, now_iso
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                return None
            run["state"] = state
            run["summary"] = summary
            run["error"] = error
            run["isolated_session_id"] = isolated_session_id
            self.finished_count += 1
            self.finished.set()
            return dict(run)

    def mark_cron_delivery_target(self, run_id: str, *, target: str) -> bool:
        with self._lock:
            seen = self.delivery_targets.setdefault(run_id, set())
            if target in seen:
                return False
            seen.add(target)
            return True

    def delete_cron_job(self, job_id: str) -> None:
        with self._lock:
            self.deleted_job_ids.append(job_id)
            self.jobs.pop(job_id, None)


def test_scheduler_executes_agent_turn_and_calls_delivery() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-1",
        payload={"kind": "agentTurn", "message": "Summarize overnight updates."},
        delivery={"mode": "announce", "to": "cli:ops"},
    )
    store.seed_due("job-1")
    deliveries: list[tuple[str, str]] = []

    def _exec_agent(job: dict, run: dict) -> dict:  # noqa: ANN001
        del job
        return {"summary": f"done:{run['run_id']}", "isolated_session_id": "sess-iso-1"}

    def _deliver(mode: str, to_value: str, job: dict, run: dict, result) -> None:  # noqa: ANN001
        del job, run, result
        deliveries.append((mode, to_value))

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-1",
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=2,
        execute_agent_turn=_exec_agent,
        delivery_handler=_deliver,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    run = next(iter(store.runs.values()))
    assert run["state"] == "finished"
    assert run["summary"].startswith("done:")
    assert deliveries == [("announce", "cli:ops")]


def test_scheduler_respects_global_concurrency_limit() -> None:
    store = FakeCronStore()
    store.add_job(job_id="job-a", payload={"kind": "agentTurn", "message": "a"})
    store.add_job(job_id="job-b", payload={"kind": "agentTurn", "message": "b"})
    store.seed_due("job-a")
    store.seed_due("job-b")

    active = {"count": 0, "max": 0}
    lock = threading.RLock()

    def _exec_agent(job: dict, run: dict) -> str:  # noqa: ANN001
        del job, run
        with lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        sleep(0.2)
        with lock:
            active["count"] -= 1
        return "ok"

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-2",
        tick_seconds=0.02,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        execute_agent_turn=_exec_agent,
    )
    scheduler.start()
    try:
        deadline = 5.0
        elapsed = 0.0
        while elapsed < deadline:
            if store.finished_count >= 2:
                break
            sleep(0.05)
            elapsed += 0.05
        assert store.finished_count >= 2
    finally:
        scheduler.shutdown(grace_s=1.0)

    assert active["max"] == 1


def test_scheduler_renews_lease_for_long_running_run() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-lease", payload={"kind": "agentTurn", "message": "long task"}
    )
    store.seed_due("job-lease")

    def _exec_agent(job: dict, run: dict) -> str:  # noqa: ANN001
        del job, run
        sleep(2.2)
        return "ok"

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-3",
        tick_seconds=0.05,
        lease_ttl_seconds=1,
        max_concurrent_runs=1,
        execute_agent_turn=_exec_agent,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=5.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    run_id = next(iter(store.runs.keys()))
    assert store.renew_counts.get(run_id, 0) >= 1


def test_best_effort_delivery_errors_do_not_fail_run() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-best-effort",
        payload={"kind": "agentTurn", "message": "task"},
        delivery={"mode": "announce", "best_effort": True},
    )
    store.seed_due("job-best-effort")

    def _exec_agent(job: dict, run: dict) -> str:  # noqa: ANN001
        del job, run
        return "ok"

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-4",
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        execute_agent_turn=_exec_agent,
        delivery_handler=None,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    run = next(iter(store.runs.values()))
    assert run["state"] == "finished"


def test_scheduler_skips_watch_delivery_until_requested() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-watch-open",
        payload={
            "kind": "agentTurn",
            "message": "check",
            "_openminion_watch": {"description": "watch"},
        },
        delivery={"mode": "announce", "to": "cli:ops"},
    )
    store.seed_due("job-watch-open")
    deliveries: list[tuple[str, str]] = []

    def _exec_agent(job: dict, run: dict) -> dict:  # noqa: ANN001
        del job, run
        return {
            "summary": "Still checking.",
            "output": {"watch_delivery_requested": False, "watch_terminal": False},
        }

    def _deliver(mode: str, to_value: str, job: dict, run: dict, result) -> None:  # noqa: ANN001
        del job, run, result
        deliveries.append((mode, to_value))

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-watch-open",
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        execute_agent_turn=_exec_agent,
        delivery_handler=_deliver,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    assert deliveries == []
    assert store.deleted_job_ids == []


def test_scheduler_deletes_terminal_watch_after_delivery() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-watch-terminal",
        payload={
            "kind": "agentTurn",
            "message": "check",
            "_openminion_watch": {"description": "watch"},
        },
        delivery={"mode": "announce", "to": "cli:ops"},
    )
    store.seed_due("job-watch-terminal")
    deliveries: list[tuple[str, str]] = []

    def _exec_agent(job: dict, run: dict) -> dict:  # noqa: ANN001
        del job, run
        return {
            "summary": "Condition met.",
            "output": {"watch_delivery_requested": True, "watch_terminal": True},
        }

    def _deliver(mode: str, to_value: str, job: dict, run: dict, result) -> None:  # noqa: ANN001
        del job, run, result
        deliveries.append((mode, to_value))

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-watch-terminal",
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        execute_agent_turn=_exec_agent,
        delivery_handler=_deliver,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    assert deliveries == [("announce", "cli:ops")]
    assert store.deleted_job_ids == ["job-watch-terminal"]


def test_scheduler_defaults_isolated_session_id_for_agent_turn() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-iso-default",
        payload={"kind": "agentTurn", "message": "task"},
        delivery={"mode": "none"},
    )
    store.seed_due("job-iso-default")

    def _exec_agent(job: dict, run: dict) -> dict:  # noqa: ANN001
        del job, run
        return {"summary": "ok"}

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-iso",
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        execute_agent_turn=_exec_agent,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    run = next(iter(store.runs.values()))
    assert run["state"] == "finished"
    assert run["isolated_session_id"] == f"cron:job-iso-default:{run['run_id']}"
