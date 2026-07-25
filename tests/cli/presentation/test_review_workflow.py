from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.cli.presentation.review import run_review_workflow


_BLOCKING_DIFF = (
    """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,60 +1,1 @@
"""
    + "\n".join(f"-x_{i}" for i in range(60))
    + """
+kept
"""
)

_CLEAN_DIFF = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""


def test_review_workflow_reports_no_target_when_git_diff_empty(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.cli.presentation.review.render_git_diff",
        lambda _working_dir, _args="": SimpleNamespace(
            has_diff=False,
            message="(no pending changes detected)",
        ),
    )

    result = run_review_workflow(tmp_path)

    assert result.action_result is None
    assert "no review target" in result.body


def test_review_workflow_runs_current_git_diff(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "openminion.cli.presentation.review.render_git_diff",
        lambda _working_dir, _args="": SimpleNamespace(
            has_diff=True,
            output=_CLEAN_DIFF,
        ),
    )

    result = run_review_workflow(tmp_path)

    assert result.action_result is not None
    assert "Review result (git-diff)" in result.body
    assert "severity=ok" in result.body


def test_review_workflow_runs_inline_unified_diff(tmp_path: Path) -> None:
    result = run_review_workflow(tmp_path, _BLOCKING_DIFF)

    assert result.action_result is not None
    assert "severity=block" in result.body
    assert "large_deletion" in result.body


def test_review_workflow_reads_workspace_diff_file(tmp_path: Path) -> None:
    diff_file = tmp_path / "review.diff"
    diff_file.write_text(_CLEAN_DIFF, encoding="utf-8")

    result = run_review_workflow(tmp_path, "--file review.diff")

    assert result.action_result is not None
    assert "Review result (file)" in result.body
    assert "severity=ok" in result.body


def test_review_workflow_rejects_diff_file_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.diff"
    outside.write_text(_CLEAN_DIFF, encoding="utf-8")

    result = run_review_workflow(tmp_path, f"--file {outside}")

    assert result.action_result is None
    assert "inside the workspace" in result.body
