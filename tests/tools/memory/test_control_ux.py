from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.memory import REGISTRAR
from openminion.tools.memory.control_ux import (
    MemoryControlScenario,
    format_memory_control_summary,
    run_memory_control_scenario,
)


def _memory_service(tmp_path: Path) -> tuple[MemoryService, InMemoryMemoryAuditSink]:
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(SQLiteMemoryStore(tmp_path / "memory.db"), sink=sink)
    return MemoryService(store=store, policy=PromotionPolicy()), sink


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    return registry


def _context(service: MemoryService) -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="console",
        target="focus",
        session_id="sess-memory-control",
        metadata={},
        memory_service=service,
    )


def test_control_scenario_records_typed_ids_and_audit_events(tmp_path: Path) -> None:
    service, sink = _memory_service(tmp_path)
    result = run_memory_control_scenario(
        registry=_registry(),
        context=_context(service),
        scenario=MemoryControlScenario(
            scope="session:sess-memory-control",
            record_type="fact",
            title="Preferred editor",
            content={"value": "vim"},
            search_query="vim",
            correction_title="Preferred editor correction",
            correction_content={"value": "zed"},
            forget_reason="explicit operator correction",
            tags=("preferences",),
        ),
    )

    assert result.original_record_id in result.search_record_ids
    assert result.correction_record_id != result.original_record_id
    assert result.forgotten_record_id == result.original_record_id
    assert result.forget_deleted is True
    assert result.forget_reason == "explicit operator correction"
    assert result.audit_event_types == (
        "memory.record.put",
        "memory.record.put",
        "memory.record.delete",
    )
    assert len(result.audit_event_ids) == 3
    assert sink.events[-1].to_dict()["details"]["reason"] == result.forget_reason


def test_control_summary_is_operator_readable_without_private_content(
    tmp_path: Path,
) -> None:
    service, _sink = _memory_service(tmp_path)
    result = run_memory_control_scenario(
        registry=_registry(),
        context=_context(service),
        scenario=MemoryControlScenario(
            scope="session:sess-memory-control",
            record_type="fact",
            title="Sensitive original",
            content={"secret": "do not print this"},
            search_query="secret",
            correction_title="Sensitive correction",
            correction_content={"secret": "also hidden"},
            forget_reason="explicit temp-data cleanup",
        ),
    )

    summary = format_memory_control_summary(result)

    assert f"original_record_id: {result.original_record_id}" in summary
    assert f"correction_record_id: {result.correction_record_id}" in summary
    assert "forget_deleted: true" in summary
    assert "explicit temp-data cleanup" in summary
    assert "do not print this" not in summary
    assert "also hidden" not in summary


def test_memory_forget_accepts_explicit_reason_and_preserves_audit(
    tmp_path: Path,
) -> None:
    service, sink = _memory_service(tmp_path)
    registry = _registry()
    context = _context(service)
    write_result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.write",
                arguments={
                    "scope": "session:sess-memory-control",
                    "record_type": "fact",
                    "title": "Temporary fact",
                    "content": {"value": "temporary"},
                },
                id="write",
                source="test",
            )
        ],
        context=context,
    ).results[0]
    record_id = str(write_result.data["record_id"])

    forget_result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.forget",
                arguments={
                    "record_id": record_id,
                    "reason": "operator requested forget",
                },
                id="forget",
                source="test",
            )
        ],
        context=context,
    ).results[0]

    assert forget_result.ok
    assert forget_result.data["reason"] == "operator requested forget"
    assert sink.events[-1].to_dict()["details"]["reason"] == "operator requested forget"


@pytest.mark.parametrize(
    "arguments",
    [
        {"reason": "missing explicit record id"},
        {"record_id": "", "reason": "blank record id"},
        {"record_id": "mem_123", "reason": ""},
    ],
)
def test_memory_forget_never_infers_target_or_reason_from_prose(
    tmp_path: Path,
    arguments: dict[str, str],
) -> None:
    service, _sink = _memory_service(tmp_path)
    result = (
        _registry()
        .execute_calls(
            [
                ProviderToolCall(
                    name="memory.forget",
                    arguments=arguments,
                    id="forget-invalid",
                    source="test",
                )
            ],
            context=_context(service),
        )
        .results[0]
    )

    assert result.ok is False
    assert result.data["error_code"] == "invalid_arguments"


def test_control_scenario_requires_explicit_correction_content(tmp_path: Path) -> None:
    service, _sink = _memory_service(tmp_path)

    with pytest.raises(ValueError):
        MemoryControlScenario(
            scope="session:sess-memory-control",
            record_type="fact",
            title="Original",
            content={"value": "old"},
            search_query="old",
            correction_title="Correction",
            correction_content="",  # type: ignore[arg-type]
            forget_reason="cleanup",
        )
