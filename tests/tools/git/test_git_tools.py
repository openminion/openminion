from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.git import register
from openminion.tools.git.plugin import (
    _h_blame,
    _h_diff,
    _h_log,
    _h_show,
    _h_status,
)

_GIT = shutil.which("git")


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(  # noqa: S603 - test harness; explicit argv
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _make_fixture_repo(root: Path) -> None:

    _run([_GIT, "init", "-q", "-b", "main"], cwd=root)
    _run([_GIT, "config", "user.email", "test@example.com"], cwd=root)
    _run([_GIT, "config", "user.name", "Test User"], cwd=root)
    _run([_GIT, "config", "commit.gpgsign", "false"], cwd=root)
    (root / "README.md").write_text("hello world\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "first commit"], cwd=root)


def _ctx_for_workspace(root: Path) -> object:

    policy = SimpleNamespace(
        raw={"workspace_root": str(root)},
        ensure_path_allowed=lambda *a, **k: None,
    )
    return SimpleNamespace(
        policy=policy,
        workspace=str(root),
        env={},
    )


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class RegistrationTests(unittest.TestCase):
    def test_all_five_read_only_tools_register(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        names = set(registry.list().keys())
        for tool in ("git.status", "git.diff", "git.log", "git.show", "git.blame"):
            self.assertIn(tool, names)


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class ReadOnlyHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt01-git-"))
        _make_fixture_repo(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_returns_clean_tree(self) -> None:
        result = _h_status({"path": "."}, self.ctx)
        parsed = result["parsed"]
        self.assertEqual(parsed["branch"], "main")
        self.assertEqual(parsed["ahead"], 0)
        self.assertEqual(parsed["behind"], 0)
        self.assertEqual(parsed["files"], [])

    def test_status_picks_up_untracked_file(self) -> None:
        (self.tmp / "notes.txt").write_text("scratch\n", encoding="utf-8")
        result = _h_status({"path": "."}, self.ctx)
        paths = [entry["path"] for entry in result["parsed"]["files"]]
        self.assertIn("notes.txt", paths)

    def test_log_returns_seeded_commit(self) -> None:
        result = _h_log({"limit": 5}, self.ctx)
        commits = result["parsed"]
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["subject"], "first commit")
        self.assertEqual(commits[0]["author_name"], "Test User")

    def test_show_returns_head_commit(self) -> None:
        result = _h_show({"ref": "HEAD"}, self.ctx)
        self.assertIn("first commit", result["parsed"]["output"])

    def test_show_unknown_ref_raises_git_ref_not_found(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_show({"ref": "no-such-ref-xyz"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_REF_NOT_FOUND")

    def test_diff_name_only_lists_changed_paths(self) -> None:
        # Modify the tracked file so the working tree differs from HEAD.
        (self.tmp / "README.md").write_text("hello world\nadded\n", encoding="utf-8")
        result = _h_diff({"name_only": True}, self.ctx)
        self.assertIn("README.md", result["parsed"]["changed_paths"])

    def test_diff_default_returns_unified_diff_text(self) -> None:
        (self.tmp / "README.md").write_text("hello world\nadded\n", encoding="utf-8")
        result = _h_diff({}, self.ctx)
        self.assertIn("README.md", result["parsed"]["diff_text"])
        self.assertIn("+added", result["parsed"]["diff_text"])

    def test_blame_returns_per_line_attribution(self) -> None:
        result = _h_blame({"path": "README.md"}, self.ctx)
        lines = result["parsed"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["content"], "hello world")
        self.assertEqual(lines[0]["author_name"], "Test User")


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class NotARepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Empty dir, no `git init`.
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt01-no-repo-"))
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_in_non_repo_raises_git_not_a_repository(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_status({"path": "."}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_NOT_A_REPOSITORY")
