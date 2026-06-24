from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.review.diff import (
    LARGE_DELETION_THRESHOLD,
    MANY_FILES_THRESHOLD,
    MAX_FINDINGS_RETURNED,
    ReviewFinding,
    ReviewResult,
    analyze_diff,
)


@pytest.mark.parametrize(
    "diff_text",
    [None, "", "   \n   \n", "this is not a diff\njust some prose"],
)
def test_returns_empty_for_non_diff_inputs(diff_text: str | None) -> None:
    result = analyze_diff(diff_text)
    assert isinstance(result, ReviewResult)
    assert result.file_count == 0
    assert result.findings == ()
    assert result.severity == "ok"


def test_parses_single_file_diff() -> None:
    diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 line one
-old line
+new line
+added line
"""
    result = analyze_diff(diff)
    assert result.file_count == 1
    assert result.lines_added == 2
    assert result.lines_removed == 1


def test_parses_multi_file_diff() -> None:
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old a
+new a
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-old b
+new b
"""
    result = analyze_diff(diff)
    assert result.file_count == 2
    assert result.lines_added == 2
    assert result.lines_removed == 2


def test_finds_no_test_for_new_code_when_only_source_changed() -> None:
    diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-old
+new
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "no_test_for_new_code" in kinds


def test_no_test_finding_when_test_file_present() -> None:
    diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_foo.py b/tests/test_foo.py
--- a/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -1 +1 @@
-old
+new
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "no_test_for_new_code" not in kinds


def test_no_test_finding_when_only_docs_changed() -> None:
    diff = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old text
+new text
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "no_test_for_new_code" not in kinds


def test_test_path_detection_variants() -> None:
    for test_path in (
        "src/__tests__/foo.test.ts",
        "spec/foo_spec.rb",
        "tests/test_foo.py",
        "src/foo/tests/test_foo.py",
    ):
        diff = (
            f"diff --git a/{test_path} b/{test_path}\n"
            f"--- a/{test_path}\n+++ b/{test_path}\n@@ -1 +1 @@\n-old\n+new\n"
        )
        result = analyze_diff(diff)
        kinds = [f.kind for f in result.findings]
        assert "no_test_for_new_code" not in kinds, test_path


@pytest.mark.parametrize(
    "added_line",
    ["+# TODO: refactor this", "+# FIXME(jane): broken on Windows"],
)
def test_finds_todo_or_fixme_insertion(added_line: str) -> None:
    diff = f"""diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1,2 @@
 unchanged
{added_line}
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "todo_or_fixme_introduced" in kinds


def test_does_not_flag_existing_todo_in_context() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,2 +1,2 @@
-old code
+new code
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "todo_or_fixme_introduced" not in kinds


def test_finds_large_deletion() -> None:
    deletions = "\n".join(
        f"-deleted_line_{i}" for i in range(LARGE_DELETION_THRESHOLD + 5)
    )
    diff = f"""diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,55 +1,1 @@
{deletions}
+kept line
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "large_deletion" in kinds
    assert result.severity == "block"


def test_no_large_deletion_under_threshold() -> None:
    deletions = "\n".join(f"-deleted_{i}" for i in range(5))
    diff = f"""diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,5 +1,1 @@
{deletions}
+kept
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "large_deletion" not in kinds


def test_finds_many_files_changed() -> None:
    diff_blocks = []
    for i in range(MANY_FILES_THRESHOLD + 2):
        diff_blocks.append(
            f"diff --git a/tests/test_{i}.py b/tests/test_{i}.py\n"
            f"--- a/tests/test_{i}.py\n+++ b/tests/test_{i}.py\n"
            f"@@ -1 +1 @@\n-old\n+new\n"
        )
    result = analyze_diff("\n".join(diff_blocks))
    kinds = [f.kind for f in result.findings]
    assert "many_files_changed" in kinds
    assert result.file_count == MANY_FILES_THRESHOLD + 2


def test_no_many_files_under_threshold() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""
    result = analyze_diff(diff)
    kinds = [f.kind for f in result.findings]
    assert "many_files_changed" not in kinds


def test_severity_ok_when_no_findings() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""
    result = analyze_diff(diff)
    assert result.severity == "ok"
    assert result.findings == ()


def test_severity_warn_for_no_test_finding() -> None:
    diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-old
+new
"""
    result = analyze_diff(diff)
    assert result.severity == "warn"


def test_severity_block_overrides_warn() -> None:
    deletions = "\n".join(f"-x_{i}" for i in range(LARGE_DELETION_THRESHOLD + 5))
    diff = f"""diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,55 +1,1 @@
{deletions}
+kept
"""
    result = analyze_diff(diff)
    assert result.severity == "block"


def test_findings_cap_pinned() -> None:
    assert MAX_FINDINGS_RETURNED == 32


def test_findings_capped_at_max() -> None:
    plus_lines = "\n".join(
        f"+# TODO: marker {i}" for i in range(MAX_FINDINGS_RETURNED + 5)
    )
    diff = f"""diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1,{MAX_FINDINGS_RETURNED + 6} @@
 unchanged
{plus_lines}
"""
    result = analyze_diff(diff)
    assert len(result.findings) <= MAX_FINDINGS_RETURNED


def test_summary_includes_file_count_and_line_counts() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1,2 @@
 unchanged
+added
"""
    result = analyze_diff(diff)
    assert "1 file" in result.summary
    assert "+1" in result.summary
    assert "severity=" in result.summary


def test_review_finding_defaults() -> None:
    f = ReviewFinding(kind="some_kind")
    assert f.severity == "warn"
    assert f.file == ""
    assert f.line == 0
    assert f.message == ""
