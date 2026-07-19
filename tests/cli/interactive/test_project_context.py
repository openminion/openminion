from __future__ import annotations

import importlib
import sys
from pathlib import Path

from openminion.cli.interactive.project_context import (
    build_init_template,
    build_project_context_metadata,
    resolve_project_context,
    write_init_template,
)
from openminion.services.agent.context.runtime import _append_project_context_block


def test_project_context_import_does_not_eagerly_import_textual_app() -> None:
    sys.modules.pop("openminion.cli.interactive", None)
    sys.modules.pop("openminion.cli.interactive.app", None)
    sys.modules.pop("openminion.cli.interactive.project_context", None)

    module = importlib.import_module("openminion.cli.interactive.project_context")

    assert module.__name__ == "openminion.cli.interactive.project_context"
    assert "openminion.cli.interactive.app" not in sys.modules


def test_resolve_project_context_prefers_openminion_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "OPENMINION.md").write_text("canonical body", encoding="utf-8")

    info = resolve_project_context(tmp_path)

    assert info is not None
    assert info.source_name == "OPENMINION.md"
    assert info.content == "canonical body"


def test_resolve_project_context_walks_upward(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (root / "CLAUDE.md").write_text("legacy body", encoding="utf-8")

    info = resolve_project_context(nested)

    assert info is not None
    assert info.source_name == "CLAUDE.md"
    assert info.path == root / "CLAUDE.md"


def test_resolve_project_context_truncates_large_file(tmp_path: Path) -> None:
    payload = "a" * 70000
    (tmp_path / "OPENMINION.md").write_text(payload, encoding="utf-8")

    info = resolve_project_context(tmp_path)

    assert info is not None
    assert info.truncated is True
    assert "[... project context truncated ...]" in info.content


def test_build_project_context_metadata_uses_string_values(tmp_path: Path) -> None:
    (tmp_path / "OPENMINION.md").write_text("hello", encoding="utf-8")
    info = resolve_project_context(tmp_path)

    metadata = build_project_context_metadata(info)

    assert metadata["project_context_name"] == "OPENMINION.md"
    assert metadata["project_context_body"] == "hello"
    assert metadata["project_context_path"].endswith("OPENMINION.md")


def test_build_init_template_prefills_from_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Title\n\nThis project builds widgets.\n\nMore text.",
        encoding="utf-8",
    )

    body = build_init_template(working_dir=tmp_path, agent_id="alpha")

    assert "# " in body
    assert "This project builds widgets." in body
    assert "Default agent: alpha" in body


def test_write_init_template_refuses_when_context_exists(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("already here", encoding="utf-8")

    try:
        write_init_template(working_dir=tmp_path, agent_id="alpha")
    except FileExistsError as exc:
        assert "AGENTS.md" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FileExistsError")


def test_write_init_template_creates_openminion_md(tmp_path: Path) -> None:
    target = write_init_template(working_dir=tmp_path, agent_id="alpha")

    assert target.name == "OPENMINION.md"
    assert target.is_file()
    assert "Default agent: alpha" in target.read_text(encoding="utf-8")


def test_append_project_context_block_adds_system_section() -> None:
    rendered = _append_project_context_block(
        system_prompt="Base system prompt",
        inbound_metadata={
            "project_context_name": "OPENMINION.md",
            "project_context_path": "/tmp/project/OPENMINION.md",
            "project_context_body": "Follow local rules.",
        },
    )

    assert "Base system prompt" in rendered
    assert "## Project Context File" in rendered
    assert "Follow local rules." in rendered
