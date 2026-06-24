from __future__ import annotations

import ast
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
    _h_add,
    _h_commit,
    _h_log,
    _h_stash,
    _h_status,
)

_GIT = shutil.which("git")

PLUGIN_FILE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "openminion"
    / "tools"
    / "git"
    / "plugin.py"
)


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
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
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "first commit"], cwd=root)


def _ctx_for_workspace(root: Path) -> object:
    policy = SimpleNamespace(
        raw={"workspace_root": str(root)},
        ensure_path_allowed=lambda *a, **k: None,
    )
    return SimpleNamespace(policy=policy, workspace=str(root), env={})


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class AddHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt03-add-"))
        _make_fixture_repo(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_stages_explicit_path(self) -> None:
        (self.tmp / "new.txt").write_text("alpha\n", encoding="utf-8")
        _h_add({"paths": ["new.txt"]}, self.ctx)
        # `git status` should now show `new.txt` with index status A.
        status = _h_status({"path": "."}, self.ctx)["parsed"]
        paths_with_status = {
            (entry["path"], entry["index_status"]) for entry in status["files"]
        }
        self.assertIn(("new.txt", "A"), paths_with_status)

    def test_add_multiple_paths(self) -> None:
        (self.tmp / "a.txt").write_text("a\n", encoding="utf-8")
        (self.tmp / "b.txt").write_text("b\n", encoding="utf-8")
        _h_add({"paths": ["a.txt", "b.txt"]}, self.ctx)
        status = _h_status({"path": "."}, self.ctx)["parsed"]
        names = {entry["path"] for entry in status["files"]}
        self.assertIn("a.txt", names)
        self.assertIn("b.txt", names)

    def test_add_with_empty_paths_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_add({"paths": []}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_add_with_empty_string_entry_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_add({"paths": [""]}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_add_path_outside_workspace_returns_git_path_outside_workspace(
        self,
    ) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_add({"paths": ["../outside.txt"]}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_PATH_OUTSIDE_WORKSPACE")


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class CommitHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt03-commit-"))
        _make_fixture_repo(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_then_commit_appears_in_log_with_intact_message(self) -> None:
        (self.tmp / "feature.txt").write_text("payload\n", encoding="utf-8")
        _h_add({"paths": ["feature.txt"]}, self.ctx)
        commit_result = _h_commit({"message": "add feature file"}, self.ctx)
        self.assertEqual(commit_result["parsed"]["message"], "add feature file")
        self.assertTrue(commit_result["parsed"]["sha"])
        # Log should include the new commit on top.
        log_result = _h_log({"limit": 5}, self.ctx)
        subjects = [entry["subject"] for entry in log_result["parsed"]]
        self.assertEqual(subjects[0], "add feature file")

    def test_commit_with_nothing_staged_returns_git_nothing_to_commit(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_commit({"message": "empty"}, self.ctx)
        self.assertEqual(ctx.exception.code, "GIT_NOTHING_TO_COMMIT")

    def test_commit_with_empty_message_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_commit({"message": "   "}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_commit_with_missing_message_returns_invalid_argument(self) -> None:
        with self.assertRaises(ToolRuntimeError) as ctx:
            _h_commit({}, self.ctx)
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_commit_message_with_newlines_is_passed_through(self) -> None:
        # `-m` accepts multi-line messages. Pin that we don't mangle them.
        (self.tmp / "x.txt").write_text("x\n", encoding="utf-8")
        _h_add({"paths": ["x.txt"]}, self.ctx)
        multiline = "first line\n\nbody line"
        result = _h_commit({"message": multiline}, self.ctx)
        self.assertEqual(result["parsed"]["message"], multiline)


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class StashHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ngt03-stash-"))
        _make_fixture_repo(self.tmp)
        self.ctx = _ctx_for_workspace(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_push_then_list_shows_stash(self) -> None:
        # Create an uncommitted change so push has something to stash.
        (self.tmp / "README.md").write_text("dirty\n", encoding="utf-8")
        push_result = _h_stash({"action": "push", "message": "wip change"}, self.ctx)
        self.assertFalse(push_result["parsed"]["nothing_to_stash"])

        list_result = _h_stash({"action": "list"}, self.ctx)
        stashes = list_result["parsed"]["stashes"]
        self.assertEqual(len(stashes), 1)
        self.assertEqual(stashes[0]["index"], 0)
        self.assertEqual(stashes[0]["ref"], "stash@{0}")
        self.assertIn("wip change", stashes[0]["message"])

    def test_push_with_no_changes_flags_nothing_to_stash(self) -> None:
        result = _h_stash({"action": "push"}, self.ctx)
        self.assertTrue(result["parsed"]["nothing_to_stash"])

    def test_apply_top_of_stack_after_push(self) -> None:
        (self.tmp / "README.md").write_text("dirty\n", encoding="utf-8")
        _h_stash({"action": "push", "message": "to-apply"}, self.ctx)
        # After push the working tree is clean.
        self.assertEqual(
            (self.tmp / "README.md").read_text(encoding="utf-8"), "hello\n"
        )
        result = _h_stash({"action": "apply"}, self.ctx)
        self.assertEqual(result["parsed"]["action"], "apply")
        # Apply re-introduces the changes.
        self.assertEqual(
            (self.tmp / "README.md").read_text(encoding="utf-8"), "dirty\n"
        )
        # Stash should still be present (apply doesn't drop).
        list_after = _h_stash({"action": "list"}, self.ctx)
        self.assertEqual(len(list_after["parsed"]["stashes"]), 1)

    def test_truly_unknown_stash_action_returns_invalid_argument(self) -> None:
        for forbidden in ("show", "", "rebase"):
            with self.subTest(action=forbidden):
                with self.assertRaises(ToolRuntimeError) as ctx:
                    _h_stash({"action": forbidden}, self.ctx)
                self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")


@unittest.skipIf(_GIT is None, "git binary not on PATH")
class RegistrationTests(unittest.TestCase):
    def test_add_commit_stash_register_at_write_safe(self) -> None:
        registry = ToolRegistry([])
        register(registry)
        specs = registry.list()
        for name in ("git.add", "git.commit", "git.stash"):
            self.assertIn(name, specs)


class NoVerifyComplianceTests(unittest.TestCase):
    def test_no_no_verify_literal_in_plugin_source(self) -> None:
        source = PLUGIN_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(PLUGIN_FILE))
        offenders: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "--no-verify" in node.value:
                    offenders.append((node.lineno, node.value))
        self.assertEqual(
            offenders,
            [],
            f"`--no-verify` must never appear as a string literal in "
            f"{PLUGIN_FILE.name}; found: {offenders}",
        )
