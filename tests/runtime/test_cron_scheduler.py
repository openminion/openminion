from __future__ import annotations

import threading
from time import sleep
from uuid import uuid4

from openminion.services.cron.scheduler import CronScheduler


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

    # --- methods consumed by CronTurnExecutor (TCEE-06 integration) ---

    def delete_old_cron_runs(self, cutoff: str) -> int:
        del cutoff
        return 0

    def replace_cron_job_payload(self, job_id: str, payload: dict) -> None:
        with self._lock:
            if job_id in self.jobs:
                self.jobs[job_id]["payload"] = dict(payload)


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
        delivery_handler=None,  # intentionally missing for best-effort path
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    run = next(iter(store.runs.values()))
    assert run["state"] == "finished"


def test_scheduler_emits_daemon_hosted_heartbeat_metadata() -> None:
    store = FakeCronStore()
    events: list[tuple[str, dict]] = []

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-runtime-1",
        daemon_component_id="primary",
        daemon_pid=4242,
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        on_event=lambda name, payload: events.append((name, payload)),
    )
    scheduler.start()
    try:
        deadline = 2.0
        elapsed = 0.0
        while elapsed < deadline:
            if any(name == "cron.scheduler.heartbeat" for name, _payload in events):
                break
            sleep(0.05)
            elapsed += 0.05
        assert any(name == "cron.scheduler.heartbeat" for name, _payload in events)
    finally:
        scheduler.shutdown(grace_s=1.0)

    started = next(
        payload for name, payload in events if name == "cron.scheduler.started"
    )
    heartbeat = next(
        payload for name, payload in events if name == "cron.scheduler.heartbeat"
    )
    stopped = next(
        payload for name, payload in events if name == "cron.scheduler.stopped"
    )

    assert started["daemon_id"] == "daemon-runtime-1"
    assert started["daemon_component_id"] == "primary"
    assert started["daemon_pid"] == 4242
    assert heartbeat["daemon_component_id"] == "primary"
    assert heartbeat["daemon_pid"] == 4242
    assert heartbeat["active_runs"] == 0
    assert heartbeat["lag_seconds"] >= 0.0
    assert heartbeat["tick_duration_ms"] >= 0.0
    assert heartbeat["tick_seconds"] == 0.1
    assert stopped["daemon_component_id"] == "primary"


def test_tcee_06_daemon_path_does_not_emit_cli_manual_tick_summary() -> None:
    store = FakeCronStore()
    store.add_job(
        job_id="job-tcee-06",
        payload={"kind": "agentTurn", "message": "TCEE-06 daemon proof"},
    )
    store.seed_due("job-tcee-06")

    def _exec_agent(job: dict, run: dict) -> dict:  # noqa: ANN001
        del job
        # Any non-stub summary is fine here.
        return {"summary": f"daemon-executor-run:{run['run_id']}"}

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-tcee-06",
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
    # Anti-cheat invariant per Mandatory Execution Protocol item 4.
    assert run["summary"] != "Manual tick execution"
    assert run["summary"].startswith("daemon-executor-run:")


def test_tcee_06_scheduler_invokes_real_cron_turn_executor_end_to_end() -> None:
    from types import SimpleNamespace

    from openminion.services.runtime.cron_delivery import CronDeliveryBridge
    from openminion.services.runtime.cron_executor import CronTurnExecutor

    # --- Production-shape runtime fakes (mirroring test_cron_turn_executor) ---

    class _FakeHandle:
        def __init__(self, response: object) -> None:
            self._response = response

        def result(self, timeout_s: float = 0) -> object:  # noqa: ARG002
            return self._response

    class _FakeRuntimeManager:
        def __init__(self) -> None:
            self.submitted: list[object] = []

        def submit_turn(self, request):  # noqa: ANN001
            self.submitted.append(request)
            return _FakeHandle(SimpleNamespace(final_text="cron-tcee-06 ok"))

    class _FakeSessions:
        def __init__(self) -> None:
            self.messages: list[dict] = []
            self.events: list[dict] = []

        def append_message(self, **kwargs):  # noqa: ANN003
            self.messages.append(dict(kwargs))
            return SimpleNamespace(id="msg-1")

        def append_event(self, **kwargs):  # noqa: ANN003
            self.events.append(dict(kwargs))
            return SimpleNamespace(id="evt-1")

    runtime_manager = _FakeRuntimeManager()
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            agent=SimpleNamespace(name="agent-tcee-06"),
            agents={"agent-tcee-06": SimpleNamespace(name="agent-tcee-06")},
            default_agent="agent-tcee-06",
        ),
        runtime_manager=runtime_manager,
        list_registered_agents=(lambda: ["agent-tcee-06"]),
        sessions=_FakeSessions(),
    )

    def _request_builder(payload: dict, agent_id: str) -> object:
        return SimpleNamespace(
            agent_id=agent_id,
            session_id=str(payload.get("session_id") or ""),
            trace_id=str(payload.get("trace_id") or ""),
            meta=dict(payload.get("meta") or {}),
            payload=dict(payload),
        )

    store = FakeCronStore()
    store.add_job(
        job_id="job-tcee-06-int",
        payload={
            "kind": "agentTurn",
            "message": "tcee06 integration",
            "agent_id": "agent-tcee-06",
            "_openminion_origin": {
                "session_id": "sess-tcee-06",
                "conversation_id": "conv-tcee-06",
                "thread_id": "thread-tcee-06",
                "attach_id": "attach-tcee-06",
            },
        },
        delivery={"mode": "announce", "to": "last"},
    )
    store.seed_due("job-tcee-06-int")

    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=store,
        request_builder=_request_builder,
        timeout_s=10.0,
        max_attempts=1,
    )

    scheduler = CronScheduler(
        store=store,
        daemon_id="daemon-tcee-06-int",
        tick_seconds=0.05,
        lease_ttl_seconds=2,
        max_concurrent_runs=1,
        execute_agent_turn=executor.execute,  # production shape
        delivery_handler=CronDeliveryBridge(runtime=runtime).deliver,
    )
    scheduler.start()
    try:
        assert store.finished.wait(timeout=3.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    # 1. Real executor was invoked (proven by submit_turn record).
    assert len(runtime_manager.submitted) == 1, (
        "TCEE-06: scheduler did not invoke CronTurnExecutor.execute -> "
        "runtime_manager.submit_turn"
    )

    # 2. Daemon path completed without the CLI manual-tick stub.
    run = next(iter(store.runs.values()))
    assert run["state"] == "finished"
    assert run["summary"] != "Manual tick execution"

    # 3. Request flowed through CronTurnExecutor's request_builder with the
    # agent_id from the payload — not a synthetic shortcut.
    submitted = runtime_manager.submitted[0]
    assert submitted.agent_id == "agent-tcee-06"

    # 4. Delivery/session evidence was recorded through the same bridge the
    # daemon installs, and the run has an idempotency marker for the target.
    assert store.delivery_targets[run["run_id"]] == {"announce:last"}
    assert runtime.sessions.messages
    message = runtime.sessions.messages[-1]
    assert message["session_id"] == "sess-tcee-06"
    assert message["conversation_id"] == "conv-tcee-06"
    assert message["thread_id"] == "thread-tcee-06"
    assert message["attach_id"] == "attach-tcee-06"
    assert message["metadata"]["cron_run_id"] == run["run_id"]
    assert runtime.sessions.events
    event = runtime.sessions.events[-1]
    assert event["event_type"] == "cron.announce"
    assert event["payload"]["cron_run_id"] == run["run_id"]
