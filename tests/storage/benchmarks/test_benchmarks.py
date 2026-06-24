from __future__ import annotations

import json
import os
import statistics
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote_plus

import pytest

from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.secret.storage.store import (
    PostgresSecretStore,
    SQLiteSecretStore,
)
from openminion.modules.session.storage.store import (
    PostgresSessionStore,
    SQLiteSessionStore,
)
from openminion.modules.storage.backends.postgres import (
    RecordStorePostgres,
)
from openminion.modules.telemetry.storage.store import (
    PostgresTelemetryStore,
    SQLiteTelemetryStore,
)

pytestmark = pytest.mark.postgres


ROWS_PER_MODULE = 1000
BENCHMARK_ITERATIONS = 3
MAX_REGRESSION_RATIO = 0.20
MIN_ABSOLUTE_BUDGET_MS = 1.0
BASELINE_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "trackers"
    / "artifacts"
    / "storage-benchmarks"
    / "baseline.json"
)
MODULES = ("secret", "session", "telemetry", "memory")
OPERATIONS = ("create", "read", "update", "delete")
pytestmark = pytest.mark.timeout(300)
BASELINE_SAMPLE_RUNS = 3


def _schema_url(base_url: str, schema_name: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={quote_plus(f'-csearch_path={schema_name}')}"


def _postgres_url() -> str | None:
    value = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    return value or None


@contextmanager
def _open_postgres_engine(prefix: str) -> Iterator[Any]:
    postgres_url = _postgres_url()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"{prefix}_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sqlalchemy.create_engine(
        _schema_url(postgres_url, schema_name), future=True
    )
    try:
        yield engine
    finally:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        with admin_engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        admin_engine.dispose()


@contextmanager
def _open_secret_store(backend: str, tmp_path: Path):
    if backend == "sqlite":
        store = SQLiteSecretStore(tmp_path / f"secret-{uuid.uuid4().hex}.db")
        try:
            yield store
        finally:
            store.close()
        return
    with _open_postgres_engine("sop_bench_secret") as engine:
        record_store = RecordStorePostgres(engine)
        store = PostgresSecretStore(record_store=record_store)
        try:
            yield store
        finally:
            store.close()
            record_store.close()


@contextmanager
def _open_session_store(backend: str, tmp_path: Path):
    db_path = tmp_path / f"session-{uuid.uuid4().hex}.db"
    if backend == "sqlite":
        store = SQLiteSessionStore(db_path)
        try:
            yield store
        finally:
            store.close()
        return
    with _open_postgres_engine("sop_bench_session") as engine:
        record_store = RecordStorePostgres(engine)
        store = PostgresSessionStore(db_path, record_store=record_store)
        try:
            yield store
        finally:
            store.close()
            record_store.close()


@contextmanager
def _open_telemetry_store(backend: str, tmp_path: Path):
    if backend == "sqlite":
        store = SQLiteTelemetryStore(tmp_path / f"telemetry-{uuid.uuid4().hex}.db")
        try:
            yield store
        finally:
            store.close()
        return
    with _open_postgres_engine("sop_bench_telemetry") as engine:
        record_store = RecordStorePostgres(engine)
        store = PostgresTelemetryStore(record_store=record_store)
        try:
            yield store
        finally:
            store.close()
            record_store.close()


@contextmanager
def _open_memory_store(backend: str, tmp_path: Path):
    db_path = tmp_path / f"memory-{uuid.uuid4().hex}.db"
    if backend == "sqlite":
        store = SQLiteMemoryStore(db_path)
        try:
            yield store
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()
        return
    with _open_postgres_engine("sop_bench_memory") as engine:
        store = PostgresMemoryStore(engine, database_path=db_path)
        try:
            yield store
        finally:
            store.close()


def _time_operation_ms(fn: Callable[[], None]) -> float:
    import time

    # Warm one untimed run so one-off connection/bootstrap jitter does not
    # dominate the sampled medians for short local benchmark suites.
    fn()
    durations: list[float] = []
    for _ in range(BENCHMARK_ITERATIONS):
        started = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - started) * 1000.0)
    return round(float(statistics.median(durations)), 3)


def _secret_timings(backend: str, tmp_path: Path) -> dict[str, float]:
    def create_run() -> None:
        with _open_secret_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    key=f"key-{idx}",
                    namespace="default",
                    value=f"value-{idx}",
                    created_at=float(idx),
                    updated_at=float(idx),
                )

    def read_run() -> None:
        with _open_secret_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    key=f"key-{idx}",
                    namespace="default",
                    value=f"value-{idx}",
                    created_at=float(idx),
                    updated_at=float(idx),
                )
            for idx in range(ROWS_PER_MODULE):
                assert store.fetch_value(key=f"key-{idx}", namespace="default")

    def update_run() -> None:
        with _open_secret_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    key=f"key-{idx}",
                    namespace="default",
                    value=f"value-{idx}",
                    created_at=float(idx),
                    updated_at=float(idx),
                )
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    key=f"key-{idx}",
                    namespace="default",
                    value=f"updated-{idx}",
                    created_at=float(idx),
                    updated_at=float(idx) + 1.0,
                )

    def delete_run() -> None:
        with _open_secret_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    key=f"key-{idx}",
                    namespace="default",
                    value=f"value-{idx}",
                    created_at=float(idx),
                    updated_at=float(idx),
                )
            for idx in range(ROWS_PER_MODULE):
                store.delete(key=f"key-{idx}", namespace="default")

    return {
        "create_ms": _time_operation_ms(create_run),
        "read_ms": _time_operation_ms(read_run),
        "update_ms": _time_operation_ms(update_run),
        "delete_ms": _time_operation_ms(delete_run),
    }


def _session_timings(backend: str, tmp_path: Path) -> dict[str, float]:
    def create_run() -> None:
        with _open_session_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.create_session(
                    session_id=f"session-{idx}",
                    initial_agent_id="bench-agent",
                    status="active",
                )

    def read_run() -> None:
        with _open_session_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.create_session(
                    session_id=f"session-{idx}",
                    initial_agent_id="bench-agent",
                    status="active",
                )
            for idx in range(ROWS_PER_MODULE):
                assert store.get_session(f"session-{idx}") is not None

    def update_run() -> None:
        with _open_session_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.create_session(
                    session_id=f"session-{idx}",
                    initial_agent_id="bench-agent",
                    status="active",
                )
            for idx in range(ROWS_PER_MODULE):
                store.update_session_status(f"session-{idx}", "idle")

    def delete_run() -> None:
        with _open_session_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.create_session(
                    session_id=f"session-{idx}",
                    initial_agent_id="bench-agent",
                    status="active",
                )
            for idx in range(ROWS_PER_MODULE):
                store.archive_session(f"session-{idx}")

    return {
        "create_ms": _time_operation_ms(create_run),
        "read_ms": _time_operation_ms(read_run),
        "update_ms": _time_operation_ms(update_run),
        "delete_ms": _time_operation_ms(delete_run),
    }


def _telemetry_timings(backend: str, tmp_path: Path) -> dict[str, float]:
    def create_run() -> None:
        with _open_telemetry_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.insert_event(
                    session_id=f"session-{idx}",
                    turn_id=f"turn-{idx}",
                    event_type="bench",
                    timestamp=float(idx),
                    data={"index": idx},
                )

    def read_run() -> None:
        with _open_telemetry_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.insert_event(
                    session_id=f"session-{idx}",
                    turn_id=f"turn-{idx}",
                    event_type="bench",
                    timestamp=float(idx),
                    data={"index": idx},
                )
            for idx in range(ROWS_PER_MODULE):
                rows = store.fetch_session_events(f"session-{idx}")
                assert len(rows) == 1

    def update_run() -> None:
        with _open_telemetry_store(backend, tmp_path) as store:
            record_store = store._record_store  # noqa: SLF001
            for idx in range(ROWS_PER_MODULE):
                store.insert_event(
                    session_id=f"session-{idx}",
                    turn_id=f"turn-{idx}",
                    event_type="bench",
                    timestamp=float(idx),
                    data={"index": idx},
                )
            for idx in range(ROWS_PER_MODULE):
                record_store.update_rows(
                    "events",
                    {"session_id": f"session-{idx}", "turn_id": f"turn-{idx}"},
                    {"data": json.dumps({"index": idx, "updated": True})},
                )

    def delete_run() -> None:
        with _open_telemetry_store(backend, tmp_path) as store:
            record_store = store._record_store  # noqa: SLF001
            for idx in range(ROWS_PER_MODULE):
                store.insert_event(
                    session_id=f"session-{idx}",
                    turn_id=f"turn-{idx}",
                    event_type="bench",
                    timestamp=float(idx),
                    data={"index": idx},
                )
            for idx in range(ROWS_PER_MODULE):
                record_store.delete_rows(
                    "events",
                    where={"session_id": f"session-{idx}", "turn_id": f"turn-{idx}"},
                )

    return {
        "create_ms": _time_operation_ms(create_run),
        "read_ms": _time_operation_ms(read_run),
        "update_ms": _time_operation_ms(update_run),
        "delete_ms": _time_operation_ms(delete_run),
    }


def _memory_timings(backend: str, tmp_path: Path) -> dict[str, float]:
    def create_run() -> None:
        with _open_memory_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    "session:bench",
                    "pin",
                    f"key-{idx}",
                    {"content": f"value-{idx}"},
                )

    def read_run() -> None:
        with _open_memory_store(backend, tmp_path) as store:
            record_ids: list[str] = []
            for idx in range(ROWS_PER_MODULE):
                record = store.upsert(
                    "session:bench",
                    "pin",
                    f"key-{idx}",
                    {"content": f"value-{idx}"},
                )
                record_ids.append(record.id)
            for record_id in record_ids:
                assert store.get(record_id) is not None

    def update_run() -> None:
        with _open_memory_store(backend, tmp_path) as store:
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    "session:bench",
                    "pin",
                    f"key-{idx}",
                    {"content": f"value-{idx}"},
                )
            for idx in range(ROWS_PER_MODULE):
                store.upsert(
                    "session:bench",
                    "pin",
                    f"key-{idx}",
                    {"content": f"updated-{idx}"},
                )

    def delete_run() -> None:
        with _open_memory_store(backend, tmp_path) as store:
            record_ids: list[str] = []
            for idx in range(ROWS_PER_MODULE):
                record = store.upsert(
                    "session:bench",
                    "pin",
                    f"key-{idx}",
                    {"content": f"value-{idx}"},
                )
                record_ids.append(record.id)
            for record_id in record_ids:
                store.delete(record_id)

    return {
        "create_ms": _time_operation_ms(create_run),
        "read_ms": _time_operation_ms(read_run),
        "update_ms": _time_operation_ms(update_run),
        "delete_ms": _time_operation_ms(delete_run),
    }


def run_benchmarks(tmp_path: Path) -> dict[str, Any]:
    results: dict[str, Any] = {
        "schema_version": 1,
        "rows_per_module": ROWS_PER_MODULE,
        "iterations": BENCHMARK_ITERATIONS,
        "backends": {},
    }
    backend_order = ["sqlite"]
    if _postgres_url():
        backend_order.append("postgres")

    for backend in backend_order:
        results["backends"][backend] = {
            "secret": _secret_timings(backend, tmp_path),
            "session": _session_timings(backend, tmp_path),
            "telemetry": _telemetry_timings(backend, tmp_path),
            "memory": _memory_timings(backend, tmp_path),
        }
    return results


def load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def write_baseline(results: dict[str, Any], path: Path = BASELINE_PATH) -> None:
    path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _summarize_baseline_samples(
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    if not samples:
        raise ValueError("at least one benchmark sample is required")

    backends: dict[str, Any] = {}
    for backend in samples[0]["backends"]:
        backends[backend] = {}
        for module_name in MODULES:
            backends[backend][module_name] = {}
            for metric_name in ("create_ms", "read_ms", "update_ms", "delete_ms"):
                metric_samples = [
                    float(sample["backends"][backend][module_name][metric_name])
                    for sample in samples
                ]
                backends[backend][module_name][metric_name] = {
                    "median_ms": round(float(statistics.median(metric_samples)), 3),
                    "max_ms": round(float(max(metric_samples)), 3),
                    "samples_ms": [round(float(value), 3) for value in metric_samples],
                }
    return {
        "schema_version": 2,
        "rows_per_module": ROWS_PER_MODULE,
        "iterations": BENCHMARK_ITERATIONS,
        "baseline_sample_runs": len(samples),
        "backends": backends,
    }


def generate_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for _ in range(BASELINE_SAMPLE_RUNS):
        with tempfile.TemporaryDirectory(prefix="openminion-storage-bench-") as tmp:
            samples.append(run_benchmarks(Path(tmp)))
    results = _summarize_baseline_samples(samples)
    write_baseline(results, path)
    return results


def regression_budget_ms(baseline_ms: float) -> float:
    return max(baseline_ms * MAX_REGRESSION_RATIO, MIN_ABSOLUTE_BUDGET_MS)


def _baseline_metric_window(metric: Any) -> tuple[float, float]:
    if isinstance(metric, (int, float)):
        baseline_ms = float(metric)
        return baseline_ms, baseline_ms + regression_budget_ms(baseline_ms)
    if isinstance(metric, dict):
        median_ms = float(metric["median_ms"])
        max_ms = float(metric["max_ms"])
        return median_ms, max(max_ms, median_ms + regression_budget_ms(median_ms))
    raise TypeError(f"unsupported baseline metric payload: {metric!r}")


def compare_against_baseline(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    for backend, module_map in current.get("backends", {}).items():
        baseline_backend = baseline.get("backends", {}).get(backend)
        if not isinstance(baseline_backend, dict):
            failures.append(f"missing backend baseline: {backend}")
            continue
        for module_name, timings in module_map.items():
            baseline_timings = baseline_backend.get(module_name)
            if not isinstance(baseline_timings, dict):
                failures.append(f"missing module baseline: {backend}/{module_name}")
                continue
            for metric_name, current_value in timings.items():
                baseline_value = baseline_timings.get(metric_name)
                if not isinstance(baseline_value, (int, float, dict)):
                    failures.append(
                        f"missing metric baseline: {backend}/{module_name}/{metric_name}"
                    )
                    continue
                baseline_ms, allowed_ms = _baseline_metric_window(baseline_value)
                if float(current_value) > allowed_ms:
                    failures.append(
                        f"{backend}/{module_name}/{metric_name}: current={current_value}ms "
                        f"baseline={baseline_ms}ms ceiling={allowed_ms:.3f}ms"
                    )
    return failures


def _failure_metric_keys(failures: list[str]) -> set[str]:
    return {item.split(":", 1)[0] for item in failures}


def test_baseline_file_shape() -> None:
    baseline = load_baseline()
    assert baseline["schema_version"] == 2
    assert baseline["rows_per_module"] == ROWS_PER_MODULE
    assert baseline["iterations"] == BENCHMARK_ITERATIONS
    assert baseline["baseline_sample_runs"] == BASELINE_SAMPLE_RUNS
    for backend in ("sqlite", "postgres"):
        assert backend in baseline["backends"]
        for module_name in MODULES:
            metrics = baseline["backends"][backend][module_name]
            assert set(metrics.keys()) == {
                "create_ms",
                "read_ms",
                "update_ms",
                "delete_ms",
            }
            for metric_payload in metrics.values():
                assert set(metric_payload.keys()) == {
                    "median_ms",
                    "max_ms",
                    "samples_ms",
                }
                assert len(metric_payload["samples_ms"]) == BASELINE_SAMPLE_RUNS


def test_regression_detector_rejects_simulated_slowdown() -> None:
    baseline = {
        "backends": {
            "sqlite": {
                "secret": {
                    "create_ms": {
                        "median_ms": 10.0,
                        "max_ms": 10.5,
                        "samples_ms": [9.8, 10.0, 10.5],
                    },
                    "read_ms": {
                        "median_ms": 10.0,
                        "max_ms": 10.5,
                        "samples_ms": [9.9, 10.0, 10.5],
                    },
                }
            }
        }
    }
    current = {"backends": {"sqlite": {"secret": {"create_ms": 13.5, "read_ms": 10.5}}}}
    failures = compare_against_baseline(current, baseline)
    assert len(failures) == 1
    assert "sqlite/secret/create_ms" in failures[0]


def test_regression_detector_accepts_clean_baseline() -> None:
    baseline = {
        "backends": {
            "sqlite": {
                "secret": {
                    "create_ms": {
                        "median_ms": 10.0,
                        "max_ms": 10.5,
                        "samples_ms": [9.8, 10.0, 10.5],
                    },
                    "read_ms": {
                        "median_ms": 10.0,
                        "max_ms": 10.5,
                        "samples_ms": [9.9, 10.0, 10.5],
                    },
                }
            }
        }
    }
    current = {"backends": {"sqlite": {"secret": {"create_ms": 11.5, "read_ms": 10.8}}}}
    assert compare_against_baseline(current, baseline) == []


@pytest.mark.benchmark
def test_storage_benchmarks_against_baseline(tmp_path: Path) -> None:
    baseline = load_baseline()
    current = run_benchmarks(tmp_path)
    print(json.dumps(current, indent=2, sort_keys=True))
    failures = compare_against_baseline(current, baseline)
    if not failures:
        return

    retry_current = run_benchmarks(tmp_path)
    print(json.dumps({"retry": retry_current}, indent=2, sort_keys=True))
    retry_failures = compare_against_baseline(retry_current, baseline)
    persistent_keys = _failure_metric_keys(failures) & _failure_metric_keys(
        retry_failures
    )
    if not persistent_keys:
        return

    persistent_details = [
        item for item in retry_failures if item.split(":", 1)[0] in persistent_keys
    ]
    assert persistent_details == [], (
        "benchmark regressions detected after retry:\n" + "\n".join(persistent_details)
    )
