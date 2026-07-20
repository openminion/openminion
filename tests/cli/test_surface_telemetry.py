from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.status.surface import record_surface_event
from openminion.modules.telemetry.events import catalog


class _Recorder:
    def __init__(self) -> None:
        self.events = []

    def record_event_sync(self, event) -> None:
        self.events.append(event)


def _runtime() -> tuple[SimpleNamespace, _Recorder]:
    recorder = _Recorder()
    runtime = SimpleNamespace(
        session_id="session-1",
        api_runtime=SimpleNamespace(telemetry_service=recorder),
    )
    return runtime, recorder


def test_records_only_bounded_surface_fields() -> None:
    runtime, recorder = _runtime()

    assert record_surface_event(runtime)

    event = recorder.events[0]
    assert event.event_type == catalog.CLI_SURFACE_USED
    assert event.data == {"surface": "interactive"}


def test_missing_or_failing_telemetry_never_blocks_cli() -> None:
    assert not record_surface_event(object())

    class _FailingRecorder:
        def record_event_sync(self, event) -> None:
            del event
            raise RuntimeError("store unavailable")

    runtime = SimpleNamespace(telemetry_service=_FailingRecorder())
    assert not record_surface_event(runtime)
