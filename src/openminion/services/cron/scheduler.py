from dataclasses import dataclass, field
from threading import Event, RLock, Thread
from time import monotonic
from typing import TYPE_CHECKING, Any, Callable, TypeAlias
from uuid import uuid4

from openminion.modules.task.scheduling.interfaces import (
    CRON_INTERFACE_VERSION,
    CronStoreProtocol,
)

if TYPE_CHECKING:
    from openminion.services.supervision import SupervisionPolicy


def build_cron_supervision_policy(
    *,
    tick_seconds: float = 2.0,
    scheduler_lag_warn_after_seconds: float | None = None,
    scheduler_lag_fail_after_seconds: float | None = None,
    stale_heartbeat_warn_after_seconds: float | None = None,
    stale_heartbeat_fail_after_seconds: float | None = None,
    restart_enabled: bool = False,
) -> "SupervisionPolicy":
    from openminion.services.supervision import SupervisionPolicy

    interval = max(0.1, float(tick_seconds))
    lag_warn_after = scheduler_lag_warn_after_seconds
    if lag_warn_after is None:
        lag_warn_after = interval * 2.0
    lag_fail_after = scheduler_lag_fail_after_seconds
    if lag_fail_after is None:
        lag_fail_after = interval * 4.0
    stale_warn_after = stale_heartbeat_warn_after_seconds
    if stale_warn_after is None:
        stale_warn_after = interval * 3.0
    stale_fail_after = stale_heartbeat_fail_after_seconds
    if stale_fail_after is None:
        stale_fail_after = interval * 6.0
    return SupervisionPolicy(
        stale_heartbeat_warn_after_seconds=stale_warn_after,
        stale_heartbeat_fail_after_seconds=stale_fail_after,
        scheduler_lag_warn_after_seconds=lag_warn_after,
        scheduler_lag_fail_after_seconds=lag_fail_after,
        restart_enabled=restart_enabled,
    )


@dataclass(frozen=True)
class CronExecutionResult:
    summary: str = ""
    artifact_refs: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    isolated_session_id: str | None = None


CronStore: TypeAlias = CronStoreProtocol


CronExecutor = Callable[
    [dict[str, Any], dict[str, Any]], CronExecutionResult | dict[str, Any] | str | None
]
CronDeliveryHandler = Callable[
    [str, str, dict[str, Any], dict[str, Any], CronExecutionResult], None
]
CronEventHook = Callable[[str, dict[str, Any]], None]


@dataclass
class _WorkerState:
    thread: Thread
    stop_event: Event


class CronScheduler:
    contract_version = CRON_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: CronStoreProtocol,
        daemon_id: str | None = None,
        daemon_component_id: str = "primary",
        daemon_pid: int | None = None,
        tick_seconds: float = 2.0,
        lease_ttl_seconds: int = 60,
        max_concurrent_runs: int = 4,
        execute_system_event: CronExecutor | None = None,
        execute_agent_turn: CronExecutor | None = None,
        delivery_handler: CronDeliveryHandler | None = None,
        on_event: CronEventHook | None = None,
    ) -> None:
        self._store = store
        self._daemon_id = str(daemon_id or uuid4().hex).strip()
        self._daemon_component_id = str(daemon_component_id or "").strip() or "primary"
        self._daemon_pid = int(daemon_pid) if daemon_pid is not None else None
        self._tick_seconds = max(0.1, float(tick_seconds))
        self._lease_ttl_seconds = max(1, int(lease_ttl_seconds))
        self._max_concurrent_runs = max(1, int(max_concurrent_runs))
        self._execute_system_event = (
            execute_system_event or self._default_system_event_executor
        )
        self._execute_agent_turn = (
            execute_agent_turn or self._missing_agent_turn_executor
        )
        self._delivery_handler = delivery_handler
        self._on_event = on_event

        self._lock = RLock()
        self._stop_event = Event()
        self._loop_thread: Thread | None = None
        self._started = False
        self._stopped = False
        self._workers: dict[str, _WorkerState] = {}
        self._last_loop_started_monotonic: float | None = None

    @property
    def daemon_id(self) -> str:
        return self._daemon_id

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise RuntimeError("cron scheduler has been stopped")
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            self._loop_thread = Thread(
                target=self._loop,
                name="openminion-cron-scheduler",
                daemon=True,
            )
            self._loop_thread.start()
        self._emit("cron.scheduler.started", {"daemon_id": self._daemon_id})

    def shutdown(self, *, grace_s: float = 5.0) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._stop_event.set()
            loop_thread = self._loop_thread
            workers = list(self._workers.values())

        if loop_thread is not None and loop_thread.is_alive():
            loop_thread.join(timeout=max(0.0, grace_s))

        for worker in workers:
            worker.stop_event.set()
        for worker in workers:
            if worker.thread.is_alive():
                worker.thread.join(timeout=max(0.0, grace_s))

        self._emit("cron.scheduler.stopped", {"daemon_id": self._daemon_id})

    def status(self) -> dict[str, Any]:
        with self._lock:
            active_run_ids = sorted(self._workers.keys())
            return {
                "daemon_id": self._daemon_id,
                "daemon_component_id": self._daemon_component_id,
                "daemon_pid": self._daemon_pid,
                "started": self._started,
                "stopped": self._stopped,
                "active_runs": len(active_run_ids),
                "active_run_ids": active_run_ids,
                "max_concurrent_runs": self._max_concurrent_runs,
                "lease_ttl_seconds": self._lease_ttl_seconds,
                "tick_seconds": self._tick_seconds,
            }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            loop_started = monotonic()
            lag_seconds = 0.0
            if self._last_loop_started_monotonic is not None:
                lag_seconds = max(
                    0.0,
                    loop_started
                    - self._last_loop_started_monotonic
                    - self._tick_seconds,
                )
            self._last_loop_started_monotonic = loop_started
            self._reap_workers()

            with self._lock:
                capacity = max(0, self._max_concurrent_runs - len(self._workers))

            if capacity > 0:
                try:
                    self._store.enqueue_due_cron_runs(
                        self._daemon_id,
                        lease_ttl_s=self._lease_ttl_seconds,
                        max_jobs=max(1, capacity * 2),
                    )
                    runs = self._store.acquire_cron_runs(
                        self._daemon_id,
                        lease_ttl_s=self._lease_ttl_seconds,
                        limit=capacity,
                    )
                    for run in runs:
                        self._start_worker(run)
                except Exception as exc:
                    self._emit(
                        "cron.scheduler.error",
                        {"error": str(exc)},
                    )

            with self._lock:
                active_runs = len(self._workers)
            tick_duration_ms = max(0.0, (monotonic() - loop_started) * 1000.0)
            self._emit(
                "cron.scheduler.heartbeat",
                {
                    "active_runs": active_runs,
                    "lag_seconds": round(lag_seconds, 6),
                    "tick_duration_ms": round(tick_duration_ms, 3),
                    "tick_seconds": self._tick_seconds,
                },
            )

            self._stop_event.wait(self._tick_seconds)

    def _start_worker(self, run: dict[str, Any]) -> None:
        run_id = str(run.get("run_id", "")).strip()
        if not run_id:
            return
        stop_event = Event()
        thread = Thread(
            target=self._worker_main,
            kwargs={"run": dict(run), "stop_event": stop_event},
            name=f"openminion-cron-run-{run_id[:8]}",
            daemon=True,
        )
        with self._lock:
            if run_id in self._workers:
                return
            self._workers[run_id] = _WorkerState(thread=thread, stop_event=stop_event)
        thread.start()
        self._emit(
            "cron.run.started",
            {
                "run_id": run_id,
                "job_id": run.get("job_id"),
                "daemon_id": self._daemon_id,
            },
        )

    def _reap_workers(self) -> None:
        with self._lock:
            done = [
                run_id
                for run_id, worker in self._workers.items()
                if not worker.thread.is_alive()
            ]
            for run_id in done:
                self._workers.pop(run_id, None)

    def _worker_main(self, *, run: dict[str, Any], stop_event: Event) -> None:
        run_id = str(run.get("run_id", "")).strip()
        job_id = str(run.get("job_id", "")).strip()
        renew_thread = Thread(
            target=self._lease_renewer,
            kwargs={"run_id": run_id, "stop_event": stop_event},
            name=f"openminion-cron-lease-{run_id[:8]}",
            daemon=True,
        )
        renew_thread.start()

        state = "finished"
        error: dict[str, Any] | None = None
        result = CronExecutionResult()

        try:
            if not job_id:
                raise RuntimeError("cron run missing job_id")
            job = self._store.get_cron_job(job_id)
            if job is None:
                raise RuntimeError(f"cron job not found: {job_id}")
            payload = job.get("payload", {})
            if not isinstance(payload, dict):
                raise RuntimeError("cron job payload is invalid")
            kind = str(payload.get("kind", "")).strip()
            if kind == "systemEvent":
                result = self._normalize_result(self._execute_system_event(job, run))
            elif kind == "agentTurn":
                result = self._normalize_result(self._execute_agent_turn(job, run))
                if not str(result.isolated_session_id or "").strip():
                    result = CronExecutionResult(
                        summary=result.summary,
                        artifact_refs=list(result.artifact_refs),
                        output=dict(result.output),
                        isolated_session_id=f"cron:{job_id}:{run_id}",
                    )
            else:
                raise RuntimeError(f"unsupported cron payload kind: {kind}")

            self._deliver_if_needed(job=job, run=run, result=result)
        except TimeoutError as exc:
            state = "timed_out"
            error = {"code": "cron_timeout", "message": str(exc)}
        except Exception as exc:
            state = "failed"
            error = {"code": "cron_failed", "message": str(exc)}
        finally:
            stop_event.set()
            if renew_thread.is_alive():
                renew_thread.join(timeout=1.0)
            try:
                self._store.finish_cron_run(
                    run_id,
                    state=state,
                    summary=result.summary if result.summary else None,
                    artifact_refs=result.artifact_refs,
                    error=error,
                    isolated_session_id=result.isolated_session_id,
                )
            except Exception as exc:
                self._emit(
                    "cron.run.finish_error",
                    {"run_id": run_id, "error": str(exc)},
                )
            self._cleanup_if_requested(job=job, result=result)
            self._emit(
                "cron.run.finished",
                {
                    "run_id": run_id,
                    "job_id": job_id,
                    "state": state,
                    "summary": result.summary,
                    "error": error,
                },
            )

    def _lease_renewer(self, *, run_id: str, stop_event: Event) -> None:
        interval_s = max(1.0, float(self._lease_ttl_seconds) / 2.0)
        while not stop_event.wait(interval_s):
            try:
                refreshed = self._store.renew_cron_run_lease(
                    run_id,
                    daemon_id=self._daemon_id,
                    lease_ttl_s=self._lease_ttl_seconds,
                )
            except Exception as exc:
                self._emit(
                    "cron.lease.error",
                    {"run_id": run_id, "error": str(exc)},
                )
                break
            if not refreshed:
                self._emit(
                    "cron.lease.lost",
                    {"run_id": run_id, "daemon_id": self._daemon_id},
                )
                break

    def _deliver_if_needed(
        self,
        *,
        job: dict[str, Any],
        run: dict[str, Any],
        result: CronExecutionResult,
    ) -> None:
        payload = job.get("payload", {})
        if isinstance(payload, dict) and isinstance(
            payload.get("_openminion_watch"), dict
        ):
            if not bool(result.output.get("watch_delivery_requested", False)):
                return
        delivery = job.get("delivery", {})
        if not isinstance(delivery, dict):
            delivery = {}
        best_effort = bool(delivery.get("best_effort", False))
        mode = str(delivery.get("mode", "none") or "none").strip() or "none"
        if mode == "none":
            return
        if mode == "webhook" and not result.summary.strip():
            return
        try:
            if self._delivery_handler is None:
                raise RuntimeError(
                    f"delivery mode '{mode}' is configured but no delivery handler is installed"
                )

            to_value = str(delivery.get("to", "") or "").strip()
            channel = str(delivery.get("channel", "") or "").strip()
            marker = f"{mode}:{to_value or channel}"
            if not to_value and mode in {"announce", "webhook"}:
                raise RuntimeError("delivery target is required")

            marker_fn = getattr(self._store, "mark_cron_delivery_target", None)
            if callable(marker_fn):
                accepted = bool(marker_fn(str(run.get("run_id", "")), target=marker))
                if not accepted:
                    self._emit(
                        "cron.delivery.duplicate",
                        {"run_id": run.get("run_id"), "target": marker},
                    )
                    return

            self._delivery_handler(mode, to_value, job, run, result)
        except Exception as exc:
            if not best_effort:
                raise
            self._emit(
                "cron.delivery.best_effort_error",
                {
                    "run_id": run.get("run_id"),
                    "mode": mode,
                    "error": str(exc),
                },
            )

    def _cleanup_if_requested(
        self,
        *,
        job: dict[str, Any] | None,
        result: CronExecutionResult,
    ) -> None:
        if job is None:
            return
        if not bool(result.output.get("watch_terminal", False)):
            return
        job_id = str(job.get("job_id", "") or "").strip()
        if not job_id:
            return
        try:
            self._store.delete_cron_job(job_id)
        except Exception as exc:
            self._emit(
                "cron.watch.cleanup_error",
                {"job_id": job_id, "error": str(exc)},
            )

    def _default_system_event_executor(
        self,
        job: dict[str, Any],
        run: dict[str, Any],
    ) -> CronExecutionResult:
        del run
        payload = job.get("payload", {})
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("event_text", "")).strip()
        return CronExecutionResult(summary=text)

    def _missing_agent_turn_executor(
        self,
        job: dict[str, Any],
        run: dict[str, Any],
    ) -> CronExecutionResult:
        del job, run
        raise RuntimeError("agentTurn execution is not configured")

    def _normalize_result(
        self, value: CronExecutionResult | dict[str, Any] | str | None
    ) -> CronExecutionResult:
        if value is None:
            return CronExecutionResult()
        if isinstance(value, CronExecutionResult):
            return value
        if isinstance(value, str):
            return CronExecutionResult(summary=value)
        if isinstance(value, dict):
            summary = str(value.get("summary", "") or "").strip()
            artifact_refs_raw = value.get("artifact_refs", [])
            artifact_refs = (
                artifact_refs_raw if isinstance(artifact_refs_raw, list) else []
            )
            output_raw = value.get("output", {})
            output = output_raw if isinstance(output_raw, dict) else {}
            isolated_session_id_raw = value.get("isolated_session_id")
            isolated_session_id = (
                str(isolated_session_id_raw).strip()
                if isinstance(isolated_session_id_raw, str)
                and isolated_session_id_raw.strip()
                else None
            )
            return CronExecutionResult(
                summary=summary,
                artifact_refs=artifact_refs,
                output=output,
                isolated_session_id=isolated_session_id,
            )
        raise RuntimeError(f"unsupported cron execution result type: {type(value)!r}")

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        event_payload = dict(payload)
        event_payload.setdefault("daemon_id", self._daemon_id)
        event_payload.setdefault("daemon_component_id", self._daemon_component_id)
        if self._daemon_pid is not None:
            event_payload.setdefault("daemon_pid", self._daemon_pid)
        self._on_event(event_type, event_payload)
