from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.cli import _build_app
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(tmp_path: Path) -> object:
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )
    return replace(
        cfg,
        reflection=replace(
            cfg.reflection,
            reflection_enabled=True,
            reflection_interval_sessions=3,
        ),
    )


def test_operability_commands_work_against_real_memory_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_file = tmp_path / "memory_trace.jsonl"
    monkeypatch.setenv("OPENMINION_MEMORY_TRACE_FILE", str(trace_file))

    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="ops-agent",
        memory_config=_memory_config(tmp_path),
        trace_enabled=True,
    )

    adapter.record_turn(
        session_id="ops-session-1",
        run_id="run-1",
        request_id="req-1",
        channel="eval",
        target="user",
        user_message="remember: I prefer dark mode.",
        assistant_message="Noted.",
    )
    adapter.record_turn(
        session_id="ops-session-2",
        run_id="run-2",
        request_id="req-2",
        channel="eval",
        target="user",
        user_message="remember: Use tabs for indentation.",
        assistant_message="Okay.",
    )
    adapter.record_turn(
        session_id="ops-session-3",
        run_id="run-3",
        request_id="req-3",
        channel="eval",
        target="user",
        user_message="remember: Actually use spaces for indentation.",
        assistant_message="Updated.",
    )

    for index in range(3):
        service.upsert_record(
            scope="agent:ops-agent",
            record_type="session_summary",
            key=f"summary:{index}",
            record_patch={
                "title": f"summary {index}",
                "content": {
                    "decisions": [],
                    "open_questions": [],
                    "corrections": ["Use ruff rather than flake8."],
                    "topic_keywords": ["lint", "tests"],
                    "turn_count": 3,
                    "summary_text": "Use ruff rather than flake8. I prefer dark mode.",
                },
                "tags": ["session_summary"],
                "entities": ["ruff", "dark-mode"],
                "source": "validated",
                "confidence": 0.8,
            },
        )

    adapter._maybe_run_reflection()  # noqa: SLF001
    adapter.build_context(
        session_id="ops-session-4",
        user_message="what are my preferences and lint rules?",
    )
    service.upsert_record(
        scope="agent:ops-agent",
        record_type="fact",
        key="ops:history",
        record_patch={
            "title": "History v1",
            "content": "first history version",
            "confidence": 0.6,
        },
    )
    service.upsert_record(
        scope="agent:ops-agent",
        record_type="fact",
        key="ops:history",
        record_patch={
            "title": "History v2",
            "content": "second history version",
            "confidence": 0.8,
        },
    )

    corrections = service.list(
        ListQueryOptions(
            scopes=["agent:ops-agent"],
            types=["correction"],
            limit=10,
        )
    )
    assert corrections == []

    app = _build_app()
    runner = CliRunner()
    db_path = str(tmp_path / "memory.db")

    stats = runner.invoke(
        app,
        ["stats", "--scope", "agent:ops-agent", "--json", "--db", db_path],
    )
    assert stats.exit_code == 0, stats.output
    assert '"active_record_count"' in stats.output

    explain = runner.invoke(
        app,
        [
            "search",
            "dark mode",
            "--scope",
            "agent:ops-agent",
            "--explain",
            "--db",
            db_path,
        ],
    )
    assert explain.exit_code == 0, explain.output
    assert "score: unified=" in explain.output

    history = runner.invoke(
        app,
        [
            "history",
            "--scope",
            "agent:ops-agent",
            "--type",
            "fact",
            "--key",
            "ops:history",
            "--db",
            db_path,
        ],
    )
    assert history.exit_code == 0, history.output
    assert "[active]" in history.output or "[superseded]" in history.output

    export = runner.invoke(
        app,
        [
            "export",
            "--scope",
            "agent:ops-agent",
            "--type",
            "meta_insight",
            "--format",
            "json",
            "--db",
            db_path,
        ],
    )
    assert export.exit_code == 0, export.output
    assert '"meta_insight"' in export.output

    inspect = runner.invoke(
        app,
        [
            "inspect",
            "--scope",
            "agent:ops-agent",
            "--trace-file",
            str(trace_file),
            "--json",
            "--db",
            db_path,
        ],
    )
    assert inspect.exit_code == 0, inspect.output
    assert '"recent_trace_events"' in inspect.output

    trace_list = runner.invoke(
        app,
        [
            "trace",
            "list",
            "--trace-file",
            str(trace_file),
            "--limit",
            "5",
            "--json",
        ],
    )
    assert trace_list.exit_code == 0, trace_list.output
    assert "memory.context.built" in trace_list.output
