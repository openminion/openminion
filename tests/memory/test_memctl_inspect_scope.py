"""`memctl inspect` with an omitted, empty, valid or invalid `--scope`.

The tests run the real memctl app on a SQLite memory database. The
deepest-history summary does not depend on the scope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openminion.modules.memory import cli as memory_cli
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore

NO_SCOPE = {"omitted": [], "empty": ["--scope", ""], "blank": ["--scope", "  "]}
OUTPUTS = {"json": ["--json"], "human": []}


@pytest.fixture
def memory_db(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "memory.db"
    trace_file = tmp_path / "memory_trace.jsonl"
    trace_file.write_text("", encoding="utf-8")
    store = SQLiteMemoryStore(db_path)
    try:
        for record_id, scope, key in (
            ("tabs", "agent:a", None),
            ("indent-v1", "agent:history", "pref:indent"),
        ):
            store.put(
                MemoryRecord(
                    id=record_id,
                    scope=scope,
                    type="fact",
                    key=key,
                    title="Prefer tabs",
                    content="I prefer tabs.",
                    created_at="2026-10-01T00:00:00+00:00",
                    updated_at="2026-10-01T00:00:00+00:00",
                )
            )
        store.upsert(
            "agent:history",
            "fact",
            "pref:indent",
            {"title": "Prefer spaces", "content": "I prefer spaces."},
        )
    finally:
        store.close()
    return db_path, trace_file


def _memctl(*args: str):
    return CliRunner().invoke(memory_cli._build_app(), list(args))


def _inspect(memory_db: tuple[Path, Path], *args: str):
    db_path, trace_file = memory_db
    return _memctl(
        "inspect", *args, "--trace-file", str(trace_file), "--db", str(db_path)
    )


def _stats(memory_db: tuple[Path, Path], *args: str) -> dict:
    result = _memctl("stats", *args, "--json", "--db", str(memory_db[0]))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("scope_args", NO_SCOPE.values(), ids=NO_SCOPE.keys())
def test_inspect_without_a_scope_reports_every_scope_in_json(
    memory_db: tuple[Path, Path], scope_args: list[str]
) -> None:
    result = _inspect(memory_db, *scope_args, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scope"] is None
    # The default inspection identifiers: no records in agent:inspect.
    assert payload["snapshot"]["scope_filter"] == "session:inspect"
    assert payload["snapshot"]["agent_records"] == 0
    assert payload["stats"] == _stats(memory_db)
    assert payload["stats"]["active_record_count"] == 2
    assert payload["history_summary"]["key"] == "pref:indent"


@pytest.mark.parametrize("scope_args", NO_SCOPE.values(), ids=NO_SCOPE.keys())
def test_inspect_without_a_scope_reports_every_scope_in_human_output(
    memory_db: tuple[Path, Path], scope_args: list[str]
) -> None:
    result = _inspect(memory_db, *scope_args)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Memory inspect"
    assert "  scope: all" in lines
    assert "Deepest history: agent:history fact pref:indent (depth 2)" in lines


def test_inspect_with_a_valid_scope_filters_in_json(
    memory_db: tuple[Path, Path],
) -> None:
    result = _inspect(memory_db, "--scope", "agent:a", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scope"] == "agent:a"
    assert payload["snapshot"]["agent_records"] == 1
    assert payload["stats"] == _stats(memory_db, "--scope", "agent:a")
    assert payload["stats"]["active_record_count"] == 1
    assert payload["history_summary"]["key"] == "pref:indent"


def test_inspect_with_a_valid_scope_in_human_output(
    memory_db: tuple[Path, Path],
) -> None:
    result = _inspect(memory_db, "--scope", "agent:a")

    assert result.exit_code == 0, result.output
    assert "  scope: agent:a" in result.stdout.splitlines()


@pytest.mark.parametrize("output_args", OUTPUTS.values(), ids=OUTPUTS.keys())
def test_inspect_with_an_invalid_scope_is_a_cli_error(
    memory_db: tuple[Path, Path], output_args: list[str]
) -> None:
    result = _inspect(memory_db, "--scope", "not-a-scope", *output_args)

    assert result.exit_code == 1
    # A clean exit, not an exception escaping the command.
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""
    assert result.stderr == "Error: invalid scope: 'not-a-scope'\n"
    # The same presentation as memctl list for the same scope.
    listed = _memctl("list", "--scope", "not-a-scope", "--db", str(memory_db[0]))
    assert (listed.exit_code, listed.stderr) == (1, result.stderr)
