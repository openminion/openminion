from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory import cli as memory_cli
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.diagnostics.operability import resolve_trace_file_path
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryRecordStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(tmp_path: Path, *, trace_file: Path | None = None) -> object:
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )
    return replace(cfg, trace_file=trace_file)


def _seed_operability_store(db_path: Path) -> SQLiteMemoryStore:
    store = SQLiteMemoryStore(db_path)
    now = "2026-03-28T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="fact-old",
            scope="agent:test-agent",
            type="fact",
            key="pref:indent",
            title="Prefer tabs",
            content="I prefer tabs.",
            confidence=0.55,
            created_at=now,
            updated_at=now,
        )
    )
    store.put(
        MemoryRecord(
            id="fact-new",
            scope="agent:test-agent",
            type="fact",
            key="pref:indent:new",
            title="Prefer spaces",
            content="I prefer spaces.",
            confidence=0.85,
            created_at=now,
            updated_at=now,
        )
    )
    store.supersede_by_contradiction(
        "fact-old",
        "fact-new",
        reason="contradiction_detected",
    )
    store.put(
        MemoryRecord(
            id="corr-1",
            scope="agent:test-agent",
            type="correction",
            key="correction:ruff",
            title="Use ruff",
            content="Use ruff rather than flake8.",
            confidence=0.9,
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.8},
        )
    )
    store.put(
        MemoryRecord(
            id="pref-1",
            scope="agent:test-agent",
            type="user_preference",
            key="pref:theme",
            title="Theme",
            content="I prefer dark mode.",
            confidence=0.75,
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.9, "hit_count": 4},
        )
    )
    store.put(
        MemoryRecord(
            id="deleted-1",
            scope="agent:test-agent",
            type="fact",
            key="deleted:1",
            title="deleted",
            content="deleted",
            confidence=0.2,
            created_at=now,
            updated_at=now,
            is_deleted=True,
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-1",
            session_id="sess-1",
            proposed_scope="agent:test-agent",
            type="fact",
            content="Candidate fact",
            status="proposed",
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-2",
            session_id="sess-1",
            proposed_scope="agent:test-agent",
            type="fact",
            content="Approved candidate",
            status="approved",
        )
    )
    return store


def _write_trace_file(path: Path) -> None:
    events = [
        {
            "event": "memory.turn.recorded",
            "agent_id": "test-agent",
            "ts": "2026-03-28T00:00:00+00:00",
            "session_id": "sess-1",
        },
        {
            "event": "memory.context.built",
            "agent_id": "test-agent",
            "ts": "2026-03-28T00:01:00+00:00",
            "session_id": "sess-1",
        },
        {
            "event": "memory.reflection.completed",
            "agent_id": "test-agent",
            "ts": "2026-03-28T00:02:00+00:00",
            "insights_written": 1,
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_resolve_trace_file_path_respects_explicit_env_and_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = tmp_path / "explicit.jsonl"
    env_path = tmp_path / "env.jsonl"
    db_path = tmp_path / "memory.db"
    cfg = _memory_config(tmp_path, trace_file=tmp_path / "cfg.jsonl")

    assert resolve_trace_file_path(explicit_path=explicit) == explicit.resolve()

    monkeypatch.setenv("OPENMINION_MEMORY_TRACE_FILE", str(env_path))
    assert (
        resolve_trace_file_path(memory_config=cfg, db_path=db_path)
        == env_path.resolve()
    )

    monkeypatch.delenv("OPENMINION_MEMORY_TRACE_FILE")
    assert (
        resolve_trace_file_path(memory_config=cfg, db_path=db_path)
        == (tmp_path / "cfg.jsonl").resolve()
    )
    assert (
        resolve_trace_file_path(db_path=db_path)
        == (tmp_path / "memory_trace.jsonl").resolve()
    )


def test_trace_file_writer_writes_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_file = tmp_path / "trace.jsonl"
    monkeypatch.setenv("OPENMINION_MEMORY_TRACE_FILE", str(trace_file))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="trace-agent",
        memory_config=_memory_config(tmp_path),
        trace_enabled=True,
    )

    adapter._trace("memory.test.event", {"session_id": "s1", "count": 2})  # noqa: SLF001

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "memory.test.event"
    assert payload["agent_id"] == "trace-agent"
    assert payload["session_id"] == "s1"
    assert payload["count"] == 2


def test_memctl_stats_errors_for_non_sqlite_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    app = memory_cli._build_app()

    monkeypatch.setattr(
        memory_cli,
        "_get_service",
        lambda db=None: MemoryService(InMemoryRecordStore()),
    )

    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 1
    assert "stats is not supported for this memory store" in result.output


def test_memctl_operability_commands(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    trace_file = tmp_path / "memory_trace.jsonl"
    _seed_operability_store(db_path)
    _write_trace_file(trace_file)
    runner = CliRunner()
    app = memory_cli._build_app()

    stats = runner.invoke(
        app,
        ["stats", "--scope", "agent:test-agent", "--json", "--db", str(db_path)],
    )
    assert stats.exit_code == 0, stats.output
    stats_payload = json.loads(stats.output)
    assert stats_payload["candidate_counts"]["approved"] == 1
    assert stats_payload["supersession_chain_count"] >= 1

    search = runner.invoke(
        app,
        [
            "search",
            "prefer",
            "--scope",
            "agent:test-agent",
            "--explain",
            "--db",
            str(db_path),
        ],
    )
    assert search.exit_code == 0, search.output
    assert "score: unified=" in search.output

    history = runner.invoke(
        app,
        [
            "history",
            "--scope",
            "agent:test-agent",
            "--type",
            "fact",
            "--key",
            "pref:indent",
            "--db",
            str(db_path),
        ],
    )
    assert history.exit_code == 0, history.output
    assert "contradiction_detected" in history.output
    assert "[superseded]" in history.output

    export = runner.invoke(
        app,
        [
            "export",
            "--scope",
            "agent:test-agent",
            "--type",
            "correction",
            "--format",
            "json",
            "--db",
            str(db_path),
        ],
    )
    assert export.exit_code == 0, export.output
    export_payload = json.loads(export.output)
    assert len(export_payload) == 1
    assert export_payload[0]["type"] == "correction"

    inspect = runner.invoke(
        app,
        [
            "inspect",
            "--scope",
            "agent:test-agent",
            "--trace-file",
            str(trace_file),
            "--json",
            "--db",
            str(db_path),
        ],
    )
    assert inspect.exit_code == 0, inspect.output
    inspect_payload = json.loads(inspect.output)
    assert inspect_payload["stats"]["candidate_counts"]["proposed"] == 1
    assert inspect_payload["last_reflection"] == "2026-03-28T00:02:00+00:00"

    trace_list = runner.invoke(
        app,
        [
            "trace",
            "list",
            "--trace-file",
            str(trace_file),
            "--event-type",
            "memory.context.built",
            "--json",
        ],
    )
    assert trace_list.exit_code == 0, trace_list.output
    trace_payload = json.loads(trace_list.output)
    assert len(trace_payload) == 1
    assert trace_payload[0]["event"] == "memory.context.built"

    trace_tail = runner.invoke(
        app,
        [
            "trace",
            "tail",
            "--trace-file",
            str(trace_file),
            "--limit",
            "1",
        ],
    )
    assert trace_tail.exit_code == 0, trace_tail.output
    assert "memory.reflection.completed" in trace_tail.output


def test_memctl_trace_list_warns_and_exits_cleanly(tmp_path: Path) -> None:
    runner = CliRunner()
    app = memory_cli._build_app()
    missing = tmp_path / "missing.jsonl"
    result = runner.invoke(
        app,
        ["trace", "list", "--trace-file", str(missing)],
    )
    assert result.exit_code == 0
    assert "Warning: no trace events found" in result.output
