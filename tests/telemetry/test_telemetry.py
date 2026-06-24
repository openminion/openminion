import asyncio
import os
import tempfile

import pytest

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.lifecycle import (
    build_component_identity,
    build_lifecycle_telemetry_event,
)
from openminion.modules.telemetry.schemas import TelemetryEvent, calculate_cost
from openminion.modules.telemetry.service import (
    TelemetryCtl,
    TelemetryService,
    create_telemetry_adapter,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


def test_record_event(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        event = TelemetryEvent(
            session_id="test-session",
            turn_id="turn-1",
            event_type="tick",
            data={"elapsed_ms": 100.0},
        )
        await service.record_event(event)
        await service.close()

    _run(_case())


def test_get_session_summary(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)

        for i in range(3):
            await service.record_event(
                TelemetryEvent(
                    session_id="test-session",
                    turn_id=f"turn-{i}",
                    event_type="tick",
                    data={"elapsed_ms": 100.0 + i},
                )
            )

        summary = await service.get_session_summary("test-session")
        assert summary.session_id == "test-session"
        assert summary.event_count == 3
        assert summary.tick_count == 3
        assert "openminion-runtime" in summary.module_stats
        runtime_stats = summary.module_stats["openminion-runtime"]
        assert runtime_stats.event_count == 3
        assert runtime_stats.success_count == 3
        assert runtime_stats.error_count == 0
        await service.close()

    _run(_case())


def test_get_session_summary_surfaces_canonical_lifecycle_heartbeat(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        component = build_component_identity(
            component_kind="daemon",
            component_id="primary",
            scope="system",
            owner_module="openminion-runtime",
        )
        await service.record_event(
            build_lifecycle_telemetry_event(
                event_type="component.heartbeat",
                component=component,
                module_id="openminion-runtime",
                session_id="lifecycle:daemon:primary",
                turn_id="daemon:heartbeat:1",
                status="ok",
                reason="heartbeat",
                source_classification="native_canonical",
            )
        )

        summary = await service.get_session_summary("lifecycle:daemon:primary")
        assert summary.event_count == 1
        assert summary.events[0].event_type == "component.heartbeat"
        assert summary.events[0].data["component"]["component_kind"] == "daemon"
        assert summary.module_stats["openminion-runtime"].event_count == 1
        assert summary.module_stats["openminion-runtime"].success_count == 1
        assert summary.module_stats["openminion-runtime"].error_count == 0
        await service.close()

    _run(_case())


def test_get_session_summary_surfaces_canonical_lifecycle_crash(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        component = build_component_identity(
            component_kind="runtime_manager",
            component_id="primary",
            scope="system",
            owner_module="openminion-runtime",
        )
        await service.record_event(
            build_lifecycle_telemetry_event(
                event_type="component.crashed",
                component=component,
                module_id="openminion-runtime",
                session_id="lifecycle:runtime_manager:primary",
                turn_id="runtime_manager:crashed:1",
                status="error",
                reason="kill_switch",
                source_classification="native_canonical",
            )
        )

        summary = await service.get_session_summary("lifecycle:runtime_manager:primary")
        assert summary.event_count == 1
        assert summary.events[0].event_type == "component.crashed"
        assert summary.events[0].data["reason"] == "kill_switch"
        assert summary.module_stats["openminion-runtime"].event_count == 1
        assert summary.module_stats["openminion-runtime"].success_count == 0
        assert summary.module_stats["openminion-runtime"].error_count == 1
        await service.close()

    _run(_case())


def test_record_metric(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        await service.record_metric("test_metric", 42.0, {"env": "test"})
        await service.close()

    _run(_case())


def test_get_session_cost(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)

        await service.record_event(
            TelemetryEvent(
                session_id="test-session",
                turn_id="turn-1",
                event_type="llm_call",
                data={"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 200},
            )
        )

        cost_summary = await service.get_session_cost("test-session", "openai", "gpt-4")
        assert cost_summary.session_id == "test-session"
        assert cost_summary.input_tokens == 1000
        assert cost_summary.output_tokens == 500
        assert cost_summary.cached_tokens == 200
        assert cost_summary.provider == "openai"
        assert cost_summary.model == "gpt-4"
        await service.close()

    _run(_case())


def test_emit_module_stats_and_get_module_summary(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        await ctl.emit_module_stats(
            session_id="test-session",
            turn_id="turn-1",
            module_id="openminion-skill",
            status="ok",
            latency_ms=12.5,
            input_tokens=100,
            output_tokens=40,
        )
        await ctl.emit_module_stats(
            session_id="test-session",
            turn_id="turn-1",
            module_id="openminion-skill",
            status="error",
            latency_ms=5.0,
            dropped_items=2,
            truncated_items=1,
        )

        module_summary = await service.get_module_summary("test-session")
        assert "openminion-skill" in module_summary
        skill_stats = module_summary["openminion-skill"]
        assert skill_stats["event_count"] == 2
        assert skill_stats["success_count"] == 1
        assert skill_stats["error_count"] == 1
        assert skill_stats["total_input_tokens"] == 100
        assert skill_stats["total_output_tokens"] == 40
        assert skill_stats["total_dropped_items"] == 2
        assert skill_stats["total_truncated_items"] == 1
        await service.close()

    _run(_case())


def test_emit_module_operation_counts(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        await ctl.emit_module_operation(
            session_id="test-session",
            turn_id="turn-1",
            module_id="openminion-tool",
            operation="run",
        )
        await ctl.emit_module_operation(
            session_id="test-session",
            turn_id="turn-1",
            module_id="openminion-tool",
            operation="stop",
        )
        await ctl.emit_module_operation(
            session_id="test-session",
            turn_id="turn-2",
            module_id="openminion-tool",
            operation="kill",
            count=2,
            status="error",
        )

        module_summary = await service.get_module_summary("test-session")
        stats = module_summary["openminion-tool"]
        assert stats["operation_counts"]["run"] == 1
        assert stats["operation_counts"]["stop"] == 1
        assert stats["operation_counts"]["kill"] == 2
        assert stats["success_count"] == 2
        assert stats["error_count"] == 1
        await service.close()

    _run(_case())


def test_emit_module_operation_rejects_negative_count(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        with pytest.raises(ValueError, match="operation_count must be non-negative"):
            await ctl.emit_module_operation(
                session_id="test-session",
                turn_id="turn-1",
                module_id="openminion-tool",
                operation="kill",
                count=-1,
            )

        module_summary = await service.get_module_summary("test-session")
        assert module_summary == {}
        await service.close()

    _run(_case())


def test_legacy_negative_operation_count_is_clamped_during_summary(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)

        await service.record_event(
            TelemetryEvent(
                session_id="legacy-session",
                turn_id="turn-1",
                event_type="module.stats",
                data={
                    "module_id": "openminion-tool",
                    "operation": "kill",
                    "operation_count": -2,
                    "status": "ok",
                },
            )
        )

        module_summary = await service.get_module_summary("legacy-session")
        assert module_summary["openminion-tool"]["operation_counts"]["kill"] == 0
        await service.close()

    _run(_case())


def test_emit_module_counter_sums(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        await ctl.emit_module_counter(
            session_id="test-session",
            turn_id="turn-1",
            module_id="openminion-tool",
            counter_name="bytes_streamed",
            value=100.0,
        )
        await ctl.emit_module_counter(
            session_id="test-session",
            turn_id="turn-2",
            module_id="openminion-tool",
            counter_name="bytes_streamed",
            value=250.5,
        )

        module_summary = await service.get_module_summary("test-session")
        stats = module_summary["openminion-tool"]
        assert stats["custom_counter_sums"]["bytes_streamed"] == 350.5
        await service.close()

    _run(_case())


def test_emit_module_counter_rejects_negative_value(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        with pytest.raises(ValueError, match="counter_value must be non-negative"):
            await ctl.emit_module_counter(
                session_id="test-session",
                turn_id="turn-1",
                module_id="openminion-tool",
                counter_name="bytes_streamed",
                value=-1.0,
            )

        module_summary = await service.get_module_summary("test-session")
        assert module_summary == {}
        await service.close()

    _run(_case())


def test_emit_tool_exec_operation_helper(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        await ctl.emit_tool_exec_operation(
            session_id="test-session",
            turn_id="turn-1",
            operation="run",
        )
        await ctl.emit_tool_exec_operation(
            session_id="test-session",
            turn_id="turn-1",
            operation="kill",
            success=False,
        )

        module_summary = await service.get_module_summary("test-session")
        stats = module_summary["openminion-tool"]
        assert stats["operation_counts"]["run"] == 1
        assert stats["operation_counts"]["kill"] == 1
        assert stats["success_count"] == 1
        assert stats["error_count"] == 1
        await service.close()

    _run(_case())


def test_builtin_emitters_contribute_module_stats(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        await ctl.emit_tick("test-session", "turn-1", 8.0)
        await ctl.emit_tool_call("test-session", "turn-1", "exec", True)
        await ctl.emit_llm_call(
            "test-session",
            "turn-1",
            input_tokens=50,
            output_tokens=20,
            cached_tokens=5,
        )
        await ctl.emit_context_pack("test-session", "turn-1", tokens=70)

        module_summary = await service.get_module_summary("test-session")
        assert "openminion-runtime" in module_summary
        assert "openminion-tool" in module_summary
        assert "openminion-llm" in module_summary
        assert "openminion-context" in module_summary
        assert module_summary["openminion-llm"]["total_input_tokens"] == 50
        assert module_summary["openminion-llm"]["total_output_tokens"] == 20
        assert module_summary["openminion-llm"]["total_cached_tokens"] == 5
        await service.close()

    _run(_case())


def test_mode_roundtrip_and_cost_by_mode(temp_db):
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        await ctl.emit_llm_call(
            "test-session",
            "turn-1",
            input_tokens=100,
            output_tokens=25,
            cached_tokens=0,
            mode="plan",
        )
        await ctl.emit_llm_call(
            "test-session",
            "turn-2",
            input_tokens=40,
            output_tokens=10,
            cached_tokens=5,
            mode="respond",
        )

        summary = await service.get_session_summary("test-session")
        assert [
            event.mode for event in summary.events if event.event_type == "llm_call"
        ] == [
            "plan",
            "respond",
        ]

        by_mode = await service.get_session_cost_by_mode(
            "test-session", "openai", "gpt-4"
        )
        assert by_mode["plan"].input_tokens == 100
        assert by_mode["respond"].cached_tokens == 5
        await service.close()

    _run(_case())


def test_calculate_cost():
    cost = calculate_cost(1000, 500, 0, "openai", "gpt-4")
    assert cost == 0.06


def test_calculate_cost_with_cached():
    cost = calculate_cost(1000, 500, 200, "openai", "gpt-4")
    assert cost == 0.054


def test_create_telemetry_adapter():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        ctl = create_telemetry_adapter(
            db_path,
            otel_exporter_config=OTELExporterConfig(
                enabled=True,
                endpoint="",
                service_name="test-openminion",
            ),
        )
        assert isinstance(ctl, TelemetryCtl)
