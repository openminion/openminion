from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

from openminion.modules.memory.cli import _build_app
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.diagnostics.tool_failure import (
    diagnose_tool_failure_fact_poisoning,
)


def _record(
    record_id: str,
    *,
    text: str,
    tags: list[str] | None = None,
    meta: dict | None = None,
    scope: str = "session:srtf",
) -> MemoryRecord:
    now = datetime.now(timezone.utc).isoformat()
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        title=record_id,
        content={"text": text},
        created_at=now,
        updated_at=now,
        tags=list(tags or []),
        meta=dict(meta or {}),
    )


def test_diagnostic_separates_structured_from_ambiguous_text(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.put(
        _record(
            "structured-tag",
            text="Unknown tool: weather.search",
            tags=["tool_failure"],
        )
    )
    store.put(
        _record(
            "structured-meta",
            text="web.search failed",
            meta={
                "source_kind": "tool_outcome",
                "source_negative_outcome": True,
                "source_outcome_status": "failure",
            },
        )
    )
    store.put(_record("ambiguous", text="Unknown tool: weather.lookup"))
    store.put(_record("normal", text="User prefers metric weather reports."))

    report = diagnose_tool_failure_fact_poisoning(store)

    assert report.scanned_count == 4
    assert report.structured_count == 2
    assert report.ambiguous_text_count == 1
    assert {item.record_id for item in report.structured} == {
        "structured-tag",
        "structured-meta",
    }
    assert [item.record_id for item in report.ambiguous_text] == ["ambiguous"]
    assert all(not item.tombstoned for item in report.structured)
    assert store.get("normal") is not None


def test_diagnostic_tombstones_only_structured_records(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.put(
        _record(
            "structured",
            text="Tool execution failed",
            tags=["tool_outcome", "outcome:failure"],
        )
    )
    store.put(_record("ambiguous", text="Tool execution failed"))

    report = diagnose_tool_failure_fact_poisoning(
        store,
        tombstone_structured=True,
    )

    assert report.tombstoned_count == 1
    assert report.structured[0].record_id == "structured"
    assert report.structured[0].tombstoned is True
    structured = store.get("structured")
    ambiguous = store.get("ambiguous")
    assert structured is not None and structured.is_deleted is True
    assert ambiguous is not None and ambiguous.is_deleted is False


def test_diagnostic_respects_scope_filter(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.put(
        _record(
            "agent-structured",
            text="Unknown tool: weather.search",
            tags=["tool_failure"],
            scope="agent:srtf",
        )
    )
    store.put(
        _record(
            "session-structured",
            text="Unknown tool: weather.search",
            tags=["tool_failure"],
            scope="session:srtf",
        )
    )

    report = diagnose_tool_failure_fact_poisoning(
        store,
        scopes=["agent:srtf"],
    )

    assert report.scanned_count == 1
    assert [item.record_id for item in report.structured] == ["agent-structured"]


def test_cli_diagnose_tool_failures_outputs_json_and_tombstones(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    store.put(
        _record(
            "structured",
            text="Unknown tool: weather.search",
            tags=["tool_failure"],
        )
    )
    store.put(_record("ambiguous", text="Unknown tool: weather.lookup"))
    runner = CliRunner()

    result = runner.invoke(
        _build_app(),
        [
            "diagnose-tool-failures",
            "--db",
            str(db_path),
            "--json",
            "--tombstone-structured",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"structured_count": 1' in result.output
    assert '"ambiguous_text_count": 1' in result.output
    assert '"tombstoned_count": 1' in result.output
    structured = store.get("structured")
    ambiguous = store.get("ambiguous")
    assert structured is not None and structured.is_deleted is True
    assert ambiguous is not None and ambiguous.is_deleted is False
