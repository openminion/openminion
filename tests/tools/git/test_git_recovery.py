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
    _h_branch,
    _h_reflog,
    _h_reset,
    _h_stash,
)

_GIT = shutil.which("git")


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _make_fixture_repo_with_commits(root: Path) -> tuple[str, str]:

    _run([_GIT, "init", "-q", "-b", "main"], cwd=root)
    _run([_GIT, "config", "user.email", "test@example.com"], cwd=root)
    _run([_GIT, "config", "user.name", "Test User"], cwd=root)
    _run([_GIT, "config", "commit.gpgsign", "false"], cwd=root)
    (root / "README.md").write_text("v1\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "first commit"], cwd=root)
    first = _run([_GIT, "rev-parse", "HEAD"], cwd=root).stdout.strip()
    (root / "README.md").write_text("v2\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "second commit"], cwd=root)
    second = _run([_GIT, "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return first, second


def _ctx_for_workspace(root: Path) -> object:
    policy = SimpleNamespace(
        raw={"workspace_root": str(root)},
        ensure_path_allowed=lambda *a, **k: None,
    )
    return SimpleNamespace(policy=policy, workspace=str(root), env={})


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class ResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt04-reset-"))
        self.first, self.second = _make_fixture_repo_with_commits(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mixed_reset_moves_head_keeps_working_tree(self) -> None:
        result = _h_reset({"ref": self.first}, self.ctx)
        self.assertEqual(result["parsed"]["mode"], "mixed")
        head = _run([_GIT, "rev-parse", "HEAD"], cwd=self.tmp).stdout.strip()
        self.assertEqual(head, self.first)
        self.assertEqual((self.tmp / "README.md").read_text(encoding="utf-8"), "v2\n")

    def test_soft_reset_moves_head_keeps_index_and_working_tree(self) -> None:
        result = _h_reset({"ref": self.first, "mode": "soft"}, self.ctx)
        self.assertEqual(result["parsed"]["mode"], "soft")
        head = _run([_GIT, "rev-parse", "HEAD"], cwd=self.tmp).stdout.strip()
        self.assertEqual(head, self.first)

    def test_hard_reset_without_confirm_returns_destructive_not_approved(
        self,
    ) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_reset({"ref": self.first, "mode": "hard"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_DESTRUCTIVE_NOT_APPROVED")
        head = _run([_GIT, "rev-parse", "HEAD"], cwd=self.tmp).stdout.strip()
        self.assertEqual(head, self.second)

    def test_hard_reset_with_confirm_true_succeeds(self) -> None:
        result = _h_reset(
            {"ref": self.first, "mode": "hard", "confirm": True}, self.ctx
        )
        self.assertEqual(result["parsed"]["mode"], "hard")
        self.assertTrue(result["parsed"]["confirmed"])
        self.assertEqual((self.tmp / "README.md").read_text(encoding="utf-8"), "v1\n")

    def test_unknown_mode_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_reset({"ref": self.first, "mode": "frobnicate"}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_missing_ref_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_reset({"mode": "mixed"}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_unknown_ref_returns_git_ref_not_found(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_reset({"ref": "no-such-ref-xyz"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_REF_NOT_FOUND")


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class ReflogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt04-reflog-"))
        _make_fixture_repo_with_commits(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reflog_returns_structured_entries(self) -> None:
        result = _h_reflog({}, self.ctx)
        entries = result["parsed"]
        self.assertGreaterEqual(len(entries), 2)
        for entry in entries:
            self.assertIn("sha", entry)
            self.assertIn("ref", entry)
            self.assertIn("action", entry)
            self.assertTrue(entry["ref"].startswith("HEAD@{"))

    def test_reflog_limit_honored(self) -> None:
        result = _h_reflog({"limit": 1}, self.ctx)
        self.assertLessEqual(len(result["parsed"]), 1)


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class BranchForceDeleteApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt04-branch-force-"))
        _make_fixture_repo_with_commits(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)
        # Create a branch with unmerged commits so `-d` (safe) would refuse.
        _run([_GIT, "branch", "unmerged"], cwd=self.tmp)
        _run([_GIT, "checkout", "unmerged"], cwd=self.tmp)
        (self.tmp / "unmerged.txt").write_text("uniq\n", encoding="utf-8")
        _run([_GIT, "add", "unmerged.txt"], cwd=self.tmp)
        _run([_GIT, "commit", "-q", "-m", "unmerged commit"], cwd=self.tmp)
        _run([_GIT, "checkout", "main"], cwd=self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_force_delete_without_confirm_returns_destructive_not_approved(
        self,
    ) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_branch(
                {"action": "delete", "name": "unmerged", "force": True},
                self.ctx,
            )
        self.assertEqual(ctx.exception.code, "GIT_DESTRUCTIVE_NOT_APPROVED")

    def test_force_delete_with_confirm_succeeds(self) -> None:
        result = _h_branch(
            {
                "action": "delete",
                "name": "unmerged",
                "force": True,
                "confirm": True,
            },
            self.ctx,
        )
        self.assertEqual(result["parsed"]["action"], "delete")
        self.assertTrue(result["parsed"]["force"])
        self.assertTrue(result["parsed"]["confirmed"])
        list_result = _h_branch({"action": "list"}, self.ctx)
        names = {entry["name"] for entry in list_result["parsed"]["branches"]}
        self.assertNotIn("unmerged", names)


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class StashDestructiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt04-stash-"))
        _make_fixture_repo_with_commits(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _push_stash(self, message: str = "wip") -> None:
        (self.tmp / "README.md").write_text("dirty\n", encoding="utf-8")
        _h_stash({"action": "push", "message": message}, self.ctx)

    def test_pop_succeeds_when_no_conflict(self) -> None:
        self._push_stash("to-pop")
        # Working tree clean now; pop should re-apply and drop the stash.
        result = _h_stash({"action": "pop"}, self.ctx)
        self.assertEqual(result["parsed"]["action"], "pop")
        list_result = _h_stash({"action": "list"}, self.ctx)
        self.assertEqual(list_result["parsed"]["stashes"], [])

    def test_drop_without_confirm_returns_destructive_not_approved(self) -> None:
        self._push_stash("drop-target")
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_stash({"action": "drop"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_DESTRUCTIVE_NOT_APPROVED")
        list_result = _h_stash({"action": "list"}, self.ctx)
        self.assertEqual(len(list_result["parsed"]["stashes"]), 1)

    def test_drop_with_confirm_true_removes_stash(self) -> None:
        self._push_stash("drop-target")
        _h_stash({"action": "drop", "confirm": True}, self.ctx)
        list_result = _h_stash({"action": "list"}, self.ctx)
        self.assertEqual(list_result["parsed"]["stashes"], [])

    def test_clear_without_confirm_returns_destructive_not_approved(self) -> None:
        self._push_stash("first")
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_stash({"action": "clear"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_DESTRUCTIVE_NOT_APPROVED")

    def test_clear_with_confirm_true_drops_all_stashes(self) -> None:
        self._push_stash("first")
        # Modify again so there's something to stash twice.
        (self.tmp / "README.md").write_text("dirtier\n", encoding="utf-8")
        _h_stash({"action": "push", "message": "second"}, self.ctx)
        _h_stash({"action": "clear", "confirm": True}, self.ctx)
        list_result = _h_stash({"action": "list"}, self.ctx)
        self.assertEqual(list_result["parsed"]["stashes"], [])


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class RegistrationTests(unittest.TestCase):
    def test_reset_and_reflog_register(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        specs = registry.list()
        self.assertIn("git.reset", specs)
        self.assertIn("git.reflog", specs)
