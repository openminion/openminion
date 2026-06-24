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
    _h_checkout,
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


def _make_fixture_repo(root: Path) -> str:

    _run([_GIT, "init", "-q", "-b", "main"], cwd=root)
    _run([_GIT, "config", "user.email", "test@example.com"], cwd=root)
    _run([_GIT, "config", "user.name", "Test User"], cwd=root)
    _run([_GIT, "config", "commit.gpgsign", "false"], cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "first commit"], cwd=root)
    sha = _run([_GIT, "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return sha


def _ctx_for_workspace(root: Path) -> object:
    policy = SimpleNamespace(
        raw={"workspace_root": str(root)},
        ensure_path_allowed=lambda *a, **k: None,
    )
    return SimpleNamespace(policy=policy, workspace=str(root), env={})


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class BranchHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt02-branch-"))
        self.head_sha = _make_fixture_repo(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_returns_seeded_main_as_current(self) -> None:
        result = _h_branch({"action": "list"}, self.ctx)
        branches = result["parsed"]["branches"]
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["name"], "main")
        self.assertTrue(branches[0]["is_current"])

    def test_create_then_list_includes_new_branch(self) -> None:
        _h_branch({"action": "create", "name": "feature-a"}, self.ctx)
        result = _h_branch({"action": "list"}, self.ctx)
        names = {
            entry["name"]: entry["is_current"] for entry in result["parsed"]["branches"]
        }
        self.assertIn("feature-a", names)
        # Creating doesn't switch; `main` stays current.
        self.assertTrue(names["main"])
        self.assertFalse(names["feature-a"])

    def test_create_from_ref_starts_branch_at_given_ref(self) -> None:
        _h_branch(
            {"action": "create", "name": "off-head", "from_ref": self.head_sha},
            self.ctx,
        )
        # The new branch should resolve to head_sha.
        completed = subprocess.run(  # noqa: S603
            [_GIT, "rev-parse", "off-head"],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(completed.stdout.strip(), self.head_sha)

    def test_delete_round_trip_removes_branch(self) -> None:
        _h_branch({"action": "create", "name": "to-remove"}, self.ctx)
        _h_branch({"action": "delete", "name": "to-remove"}, self.ctx)
        result = _h_branch({"action": "list"}, self.ctx)
        names = {entry["name"] for entry in result["parsed"]["branches"]}
        self.assertNotIn("to-remove", names)

    def test_delete_with_force_true_returns_destructive_not_approved(self) -> None:
        _h_branch({"action": "create", "name": "force-target"}, self.ctx)
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_branch(
                {"action": "delete", "name": "force-target", "force": True}, self.ctx
            )
        self.assertEqual(ctx.exception.code, "GIT_DESTRUCTIVE_NOT_APPROVED")

    def test_create_without_name_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_branch({"action": "create"}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_delete_without_name_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_branch({"action": "delete"}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_unknown_action_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_branch({"action": "rebase"}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class CheckoutHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt02-checkout-"))
        self.head_sha = _make_fixture_repo(self.tmp)
        # Pre-create a second branch so we have somewhere to switch to.
        _run([_GIT, "branch", "feature"], cwd=self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_checkout_existing_branch_switches_and_is_not_detached(self) -> None:
        result = _h_checkout({"ref": "feature"}, self.ctx)
        self.assertEqual(result["parsed"]["current_branch"], "feature")
        self.assertFalse(result["parsed"]["detached_head"])

    def test_checkout_commit_sha_enters_detached_head(self) -> None:
        result = _h_checkout({"ref": self.head_sha}, self.ctx)
        self.assertTrue(result["parsed"]["detached_head"])
        self.assertEqual(result["parsed"]["current_branch"], "")

    def test_checkout_unknown_ref_returns_git_ref_not_found(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_checkout({"ref": "no-such-ref"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_REF_NOT_FOUND")

    def test_checkout_with_conflicting_uncommitted_changes_returns_dirty(self) -> None:
        _run([_GIT, "checkout", "feature"], cwd=self.tmp)
        (self.tmp / "README.md").write_text("feature change\n", encoding="utf-8")
        _run([_GIT, "add", "README.md"], cwd=self.tmp)
        _run([_GIT, "commit", "-q", "-m", "diverge on feature"], cwd=self.tmp)
        _run([_GIT, "checkout", "main"], cwd=self.tmp)
        # Now dirty README on main with content that conflicts with feature's version.
        (self.tmp / "README.md").write_text("main local change\n", encoding="utf-8")

        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_checkout({"ref": "feature"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_DIRTY_WORKING_TREE")


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class RegistrationTests(unittest.TestCase):
    def test_branch_and_checkout_register_at_write_safe_scope(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        specs = registry.list()
        self.assertIn("git.branch", specs)
        self.assertIn("git.checkout", specs)
