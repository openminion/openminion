from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.runtime.project_instructions import (
    read_instruction_target_snapshot,
    resolve_project_instruction_target,
    resolve_repository_root,
)


@pytest.mark.parametrize("value", [None, "", ".", "relative/path"])
def test_repository_root_never_falls_back_to_cwd(tmp_path, monkeypatch, value):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert resolve_repository_root(value) is None


def test_repository_root_requires_existing_git_directory(tmp_path):
    assert resolve_repository_root(tmp_path) is None
    assert resolve_repository_root(tmp_path / "missing") is None
    file = tmp_path / "file"
    file.write_text("not a directory")
    assert resolve_repository_root(file) is None


@pytest.mark.parametrize("git_file", [False, True])
def test_repository_root_keeps_nested_checkouts_distinct(tmp_path, git_file):
    (tmp_path / ".git").mkdir()
    child = tmp_path / "child"
    nested = child / "src"
    nested.mkdir(parents=True)
    if git_file:
        (child / ".git").write_text("gitdir: ../.git/modules/child")
    else:
        (child / ".git").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(child, target_is_directory=True)
    assert resolve_repository_root(tmp_path) == str(tmp_path)
    assert resolve_repository_root(nested) == str(child)
    assert resolve_repository_root(alias) == str(child)


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


def test_resolver_stops_when_working_directory_is_vcs_root(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("outer", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    target = resolve_project_instruction_target(repo)

    assert target.exists is False
    assert target.project_root == repo
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
