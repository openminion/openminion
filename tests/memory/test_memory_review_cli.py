from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from openminion.modules.memory.cli import _build_app, _get_service
from openminion.modules.memory.models import MemoryRecord


def _seed(db: Path) -> None:
    service = _get_service(str(db))
    service._store.put(  # noqa: SLF001
        MemoryRecord(
            id="review-record",
            scope="agent:source",
            type="fact",
            key="fact:review",
            content="private memory body",
            source="user_said",
            created_at="2026-07-20T00:00:00+00:00",
            updated_at="2026-07-20T00:00:00+00:00",
        )
    )


def test_review_cli_full_approve_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    _seed(source_db)
    runner = CliRunner()
    app = _build_app()
    artifact = tmp_path / "review.json"
    markdown = tmp_path / "review.md"
    plan = tmp_path / "plan.json"
    receipt = tmp_path / "receipt.json"

    result = runner.invoke(
        app,
        [
            "review",
            "export",
            "--scope",
            "agent:source",
            "--out",
            str(artifact),
            "--markdown-out",
            str(markdown),
            "--db",
            str(source_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert artifact.exists() and markdown.exists()
    assert "private memory body" not in result.output

    result = runner.invoke(app, ["review", "inspect", "--artifact", str(artifact)])
    assert result.exit_code == 0, result.output
    assert "section.records: 1" in result.output
    assert "private memory body" not in result.output

    result = runner.invoke(
        app,
        [
            "review",
            "plan",
            "--artifact",
            str(artifact),
            "--out",
            str(plan),
            "--scope-rewrite",
            "agent:source=agent:target",
            "--conflict",
            "error",
            "--db",
            str(target_db),
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "review",
            "decide",
            "--plan",
            str(plan),
            "--out",
            str(receipt),
            "--reviewer",
            "operator",
            "--decision",
            "approve",
            "--db",
            str(target_db),
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        [
            "review",
            "apply",
            "--artifact",
            str(artifact),
            "--plan",
            str(plan),
            "--receipt",
            str(receipt),
            "--db",
            str(target_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "applied: True" in result.output
    imported = _get_service(str(target_db)).get("review-record")
    assert imported.scope == "agent:target"
    assert imported.namespace.agent_id == "target"


def test_review_cli_reject_and_markdown_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    db = tmp_path / "memory.db"
    _seed(db)
    runner = CliRunner()
    app = _build_app()
    artifact = tmp_path / "review.json"
    markdown = tmp_path / "review.md"
    plan = tmp_path / "plan.json"
    receipt = tmp_path / "receipt.json"

    assert (
        runner.invoke(
            app,
            [
                "review",
                "export",
                "--scope",
                "agent:source",
                "--out",
                str(artifact),
                "--markdown-out",
                str(markdown),
                "--db",
                str(db),
            ],
        ).exit_code
        == 0
    )
    bad = runner.invoke(
        app,
        [
            "review",
            "plan",
            "--artifact",
            str(markdown),
            "--out",
            str(plan),
            "--db",
            str(db),
        ],
    )
    assert bad.exit_code == 1
    assert "invalid_review_document" in bad.output

    assert (
        runner.invoke(
            app,
            [
                "review",
                "plan",
                "--artifact",
                str(artifact),
                "--out",
                str(plan),
                "--db",
                str(db),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "review",
                "decide",
                "--plan",
                str(plan),
                "--out",
                str(receipt),
                "--reviewer",
                "operator",
                "--decision",
                "reject",
                "--db",
                str(db),
            ],
        ).exit_code
        == 0
    )
    result = runner.invoke(
        app,
        [
            "review",
            "apply",
            "--artifact",
            str(artifact),
            "--plan",
            str(plan),
            "--receipt",
            str(receipt),
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 1
    assert "review_rejected" in result.output

    missing = runner.invoke(
        app,
        [
            "review",
            "apply",
            "--artifact",
            str(artifact),
            "--plan",
            str(plan),
            "--db",
            str(db),
        ],
    )
    assert missing.exit_code != 0
    assert "--receipt" in missing.output
