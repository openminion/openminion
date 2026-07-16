from __future__ import annotations

from openminion.cli.interactive.terminal.streaming import (
    _DIFF_RENDER_TOOL_NAMES,
    _HUNK_HEADER_RE,
    _looks_like_unified_diff,
)


def test_real_unified_diff_detected() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,5 +1,7 @@
 def hello():
-    return 1
+    return 2
+    # added comment
 print(hello())
"""
    assert _looks_like_unified_diff(body) is True


def test_real_git_diff_with_extras_detected() -> None:
    body = """diff --git a/foo.py b/foo.py
index 1234567..abcdefg 100644
--- a/foo.py
+++ b/foo.py
@@ -10,5 +10,7 @@
 unchanged context line
-removed line
+added line
+another added line
 more context
"""
    assert _looks_like_unified_diff(body) is True


def test_multiple_hunks_detected() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 line one
-line two
+LINE TWO
@@ -10,3 +10,3 @@
 another section
-old
+new
"""
    assert _looks_like_unified_diff(body) is True


def test_diff_without_file_headers_still_detected() -> None:
    body = """@@ -1,3 +1,3 @@
 ctx
-old
+new
"""
    assert _looks_like_unified_diff(body) is True


def test_bash_output_not_detected() -> None:
    body = """total 24
drwxr-xr-x  3 user staff   96 May 10 10:00 .
drwxr-xr-x 12 user staff  384 May 10 09:00 ..
-rw-r--r--  1 user staff 1234 May 10 10:00 file.py
"""
    assert _looks_like_unified_diff(body) is False


def test_plain_prose_not_detected() -> None:
    body = "This is just a plain English paragraph with no special markers."
    assert _looks_like_unified_diff(body) is False


def test_file_content_with_plus_minus_lines_not_detected() -> None:
    body = """def calculate(x, y):
    + adding
    - subtracting
    return x + y - 1
"""
    assert _looks_like_unified_diff(body) is False


def test_hunk_header_alone_not_detected() -> None:
    body = """@@ -1,5 +1,7 @@
 unchanged line one
 unchanged line two
"""
    assert _looks_like_unified_diff(body) is False


def test_only_plus_no_minus_not_detected() -> None:
    body = """@@ -1,3 +1,5 @@
 ctx
+added
+also added
"""
    assert _looks_like_unified_diff(body) is False


def test_only_minus_no_plus_not_detected() -> None:
    body = """@@ -1,5 +1,3 @@
 ctx
-removed
-also removed
"""
    assert _looks_like_unified_diff(body) is False


def test_file_headers_only_not_detected() -> None:
    body = """--- a/foo.py
+++ b/foo.py
"""
    assert _looks_like_unified_diff(body) is False


def test_shell_prompt_prefix_not_detected() -> None:
    body = """$ git diff
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 ctx
-old
+new
"""
    assert _looks_like_unified_diff(body) is False


def test_shell_prompt_on_line_two_not_detected() -> None:
    body = """First line of output
$ ls
some content
"""
    assert _looks_like_unified_diff(body) is False


def test_shell_prompt_after_line_three_does_not_disqualify() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
$ ls
-old
+new
"""
    assert _looks_like_unified_diff(body) is True


def test_empty_body_not_detected() -> None:
    assert _looks_like_unified_diff("") is False


def test_none_body_defensive() -> None:
    assert _looks_like_unified_diff(None) is False  # type: ignore[arg-type]


def test_malformed_hunk_non_numeric_not_detected() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -abc +xyz @@
 ctx
-old
+new
"""
    assert _looks_like_unified_diff(body) is False


def test_malformed_hunk_missing_dashes_not_detected() -> None:
    body = """@@ 1,3 1,3 @@
-old
+new
"""
    assert _looks_like_unified_diff(body) is False


def test_partial_hunk_marker_not_detected() -> None:
    body = """@@ partial
-old
+new
"""
    assert _looks_like_unified_diff(body) is False


def test_diff_render_tool_names_set() -> None:
    assert _DIFF_RENDER_TOOL_NAMES == frozenset({"Edit", "Write"})


def test_hunk_header_re_matches_canonical_form() -> None:
    assert _HUNK_HEADER_RE.match("@@ -1,5 +1,7 @@")
    assert _HUNK_HEADER_RE.match("@@ -10 +10 @@")
    assert _HUNK_HEADER_RE.match("@@ -1,3 +1 @@")


def test_hunk_header_re_rejects_malformed() -> None:
    assert not _HUNK_HEADER_RE.match("@@ -abc +xyz @@")
    assert not _HUNK_HEADER_RE.match("@@ no markers @@")
    assert not _HUNK_HEADER_RE.match("@ -1 +1 @")  # single @
    assert not _HUNK_HEADER_RE.match("xx @@ -1 +1 @@")  # not at start


def test_edit_in_diff_render_set() -> None:
    assert "Edit" in _DIFF_RENDER_TOOL_NAMES


def test_write_in_diff_render_set() -> None:
    assert "Write" in _DIFF_RENDER_TOOL_NAMES


def test_read_not_in_diff_render_set() -> None:
    assert "Read" not in _DIFF_RENDER_TOOL_NAMES


def test_bash_not_in_diff_render_set() -> None:
    assert "Bash" not in _DIFF_RENDER_TOOL_NAMES
