from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.runtime.project_instructions import (
    read_instruction_target_snapshot,
    resolve_project_instruction_target,
)


def test_resolver_prefers_openminion_inside_project_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "OPENMINION.md").write_text("openminion body", encoding="utf-8")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)

    target = resolve_project_instruction_target(nested)

    assert target.path == tmp_path / "OPENMINION.md"
    assert target.project_root == tmp_path
    assert target.content == "openminion body"


def test_resolver_preserves_legacy_parent_walk_without_vcs_marker(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("legacy", encoding="utf-8")

    target = resolve_project_instruction_target(nested)

    assert target.path == tmp_path / "CLAUDE.md"
    assert target.project_root == tmp_path


def test_resolver_stops_at_vcs_root(tmp_path: Path) -> None:
    outer = tmp_path / "AGENTS.md"
    outer.write_text("outer", encoding="utf-8")
    repo = tmp_path / "repo"
    nested = repo / "a"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    target = resolve_project_instruction_target(nested)

    assert target.exists is False
    assert target.path == repo / "OPENMINION.md"


def test_resolver_rejects_unsupported_target_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported_project_instruction_target"):
        resolve_project_instruction_target(tmp_path, target_name="README.md")


def test_resolver_rejects_symlink_target(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    real = tmp_path / "real.md"
    real.write_text("body", encoding="utf-8")
    (tmp_path / "OPENMINION.md").symlink_to(real)

    with pytest.raises(ValueError, match="project_instruction_target_symlink"):
        resolve_project_instruction_target(tmp_path)


def test_snapshot_read_rejects_target_outside_review_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "OPENMINION.md"
    target.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="project_instruction_target_escape"):
        read_instruction_target_snapshot(target, project_root=repo)
