from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

import pytest

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.telemetry.storage.base import TelemetryEventConflictError
from openminion.modules.telemetry.storage.store import SQLiteTelemetryStore
from openminion.services.agent.service import AgentService
from openminion.services.agent.telemetry import InvocationLifecycleFact


class RecordingExporter:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def export(self, event: TelemetryEvent) -> bool:
        self.events.append(event)
        return True

    def delete_pending_invocation(self, invocation_id: str) -> int:
        del invocation_id
        return 0

    def close(self) -> None:
        return None


def _started_event(**overrides: object) -> TelemetryEvent:
    values: dict[str, object] = {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "event_type": "agent.invocation.started",
        "event_id": "invocation-1:start",
        "timestamp": 1.25,
        "trace_key": "trace-1",
        "invocation_id": "invocation-1",
        "execution_id": "execution-1",
        "agent_id": "agent-1",
        "data": {
            "scope": "durable",
            "source_event_id": "17",
            "parent_invocation_id": None,
            "run_id": "run-1",
            "thread_id": "thread-1",
        },
    }
    values.update(overrides)
    return TelemetryEvent(**values)


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / ".openminion" / "telemetry.db"


def test_atomic_duplicate_creates_one_row_and_one_export(tmp_path: Path) -> None:
    exporter = RecordingExporter()
    service = TelemetryService(
        db_path=_db_path(tmp_path),
        external_exporter=exporter,
    )
    event = _started_event()

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(service.record_event_sync, [event] * 16))

    assert created.count(True) == 1
    assert created.count(False) == 15
    assert len(exporter.events) == 1
    assert len(service._store.fetch_invocation_events("invocation-1")) == 1
    service.close_sync()


def test_duplicate_ignores_only_local_policy_decoration(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    first_exporter = RecordingExporter()
    first = TelemetryService(
        db_path=db_path,
        external_exporter=first_exporter,
        include_local_content=False,
    )
    event = _started_event()
    assert first.record_event_sync(event) is True
    first.close_sync()

    second_exporter = RecordingExporter()
    second = TelemetryService(
        db_path=db_path,
        external_exporter=second_exporter,
        include_local_content=True,
    )
    assert second.record_event_sync(event) is False
    assert second_exporter.events == []
    second.close_sync()


def test_duplicate_changed_structural_fact_is_a_conflict(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "telemetry.db")
    first = _started_event()
    assert store.insert_event_if_absent(first) is True

    with pytest.raises(TelemetryEventConflictError):
        store.insert_event_if_absent(
            _started_event(data={**first.data, "source_event_id": "18"})
        )

    assert store.fetch_invocation_events("invocation-1") == [first]
    store.close()


def test_explicit_sync_and_async_emission_preserve_source_facts(tmp_path: Path) -> None:
    exporter = RecordingExporter()
    service = TelemetryService(
        db_path=_db_path(tmp_path),
        external_exporter=exporter,
    )
    ctl = TelemetryCtl(service)
    payload = _started_event().data
    kwargs = {
        "event_id": "invocation-1:start",
        "timestamp": 1.25,
        "trace_key": "trace-1",
        "invocation_id": "invocation-1",
        "execution_id": "execution-1",
        "agent_id": "agent-1",
    }

    assert (
        ctl.emit_canonical_event_sync(
            "session-1",
            "turn-1",
            "agent.invocation.started",
            payload,
            **kwargs,
        )
        is True
    )
    assert (
        asyncio.run(
            ctl.emit_canonical_event(
                "session-1",
                "turn-1",
                "agent.invocation.started",
                payload,
                **kwargs,
            )
        )
        is False
    )

    stored = service._store.fetch_invocation_events("invocation-1")
    assert len(stored) == 1
    assert stored[0].event_id == "invocation-1:start"
    assert stored[0].timestamp == 1.25
    assert stored[0].trace_key == "trace-1"
    assert stored[0].execution_id == "execution-1"
    assert stored[0].agent_id == "agent-1"
    assert len(exporter.events) == 1
    service.close_sync()


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), float("-inf")])
def test_invocation_lifecycle_rejects_nonfinite_source_time(
    tmp_path: Path,
    timestamp: float,
) -> None:
    service = TelemetryService(db_path=_db_path(tmp_path))
    ctl = TelemetryCtl(service)
    with pytest.raises(ValueError, match="timestamp must be finite"):
        ctl.emit_canonical_event_sync(
            "session-1",
            "turn-1",
            "agent.invocation.started",
            _started_event().data,
            event_id="invocation-1:start",
            timestamp=timestamp,
            invocation_id="invocation-1",
        )
    service.close_sync()


def test_agent_sync_adapter_is_disabled_or_failure_safe() -> None:
    agent = object.__new__(AgentService)
    agent._identity_agent_id = "agent-1"
    agent._logger = logging.getLogger(__name__)
    fact = InvocationLifecycleFact(
        event_id="invocation-1:start",
        timestamp=1.25,
        event_type="agent.invocation.started",
        invocation_id="invocation-1",
        session_id="session-1",
        turn_id="turn-1",
        payload=_started_event().data,
    )

    agent._telemetryctl = None
    assert agent.emit_invocation_lifecycle_sync(fact) is False

    class FailingCtl:
        def emit_canonical_event_sync(self, *args: object, **kwargs: object) -> bool:
            del args, kwargs
            raise RuntimeError("storage unavailable")

    agent._telemetryctl = FailingCtl()
    assert agent.emit_invocation_lifecycle_sync(fact) is False
