from __future__ import annotations

from openminion.modules.brain.loop.tools.review_control import (
    REVIEW_TOOL_NAME,
    build_review_tool_spec,
    handle_review_tool_call,
)


# Tool spec


def test_tool_spec_has_canonical_name() -> None:
    spec = build_review_tool_spec()
    assert spec.name == REVIEW_TOOL_NAME == "review.diff"


def test_tool_spec_requires_diff_argument() -> None:
    spec = build_review_tool_spec()
    schema = spec.input_schema
    assert "diff" in schema["properties"]
    assert "diff" in schema["required"]
    assert schema["additionalProperties"] is False


def test_tool_spec_description_describes_diff_review() -> None:
    spec = build_review_tool_spec()
    assert "diff" in spec.description.lower()
    assert "review" in spec.description.lower()


# Handler — default-safe failure paths


def test_handler_rejects_missing_diff() -> None:
    result = handle_review_tool_call(loop_ctx=None, arguments={})
    assert result.status != "success"
    assert result.error is not None
    assert result.error.code == "REVIEW_MISSING_DIFF"


def test_handler_rejects_empty_string_diff() -> None:
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": ""})
    assert result.status != "success"
    assert result.error is not None
    assert result.error.code == "REVIEW_MISSING_DIFF"


def test_handler_rejects_whitespace_only_diff() -> None:
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": "   \n   \n"})
    assert result.status != "success"


def test_handler_handles_none_arguments() -> None:
    # Some upstream paths may pass None instead of an empty dict.
    result = handle_review_tool_call(loop_ctx=None, arguments=None)  # type: ignore[arg-type]
    assert result.status != "success"


# Handler — success paths


def test_handler_runs_analyzer_on_real_diff() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    assert result.status == "success"
    assert "findings_count" in result.outputs
    assert "severity" in result.outputs
    assert "review_result" in result.outputs


def test_handler_outputs_include_file_and_line_counts() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,2 +1,3 @@
 keep
-removed
+added one
+added two
"""
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    assert result.outputs["file_count"] == 1
    assert result.outputs["lines_added"] == 2
    assert result.outputs["lines_removed"] == 1


def test_handler_returns_warn_severity_for_source_without_test() -> None:
    diff = """diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-old
+new
"""
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    assert result.status == "success"
    assert result.outputs["severity"] == "warn"
    assert result.outputs["findings_count"] >= 1


def test_handler_returns_ok_severity_when_tests_present() -> None:
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
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    assert result.outputs["severity"] == "ok"


def test_handler_returns_block_severity_for_large_deletion() -> None:
    deletions = "\n".join(f"-x_{i}" for i in range(60))
    diff = f"""diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,60 +1,1 @@
{deletions}
+kept
"""
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    assert result.outputs["severity"] == "block"


def test_handler_summary_includes_severity() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    assert "severity=" in result.summary


def test_handler_review_result_is_json_serializable() -> None:
    diff = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1 @@
-old
+new
"""
    result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    payload = result.outputs["review_result"]
    # Verify it's a dict (JSON-serializable shape).
    assert isinstance(payload, dict)
    assert "findings" in payload
    assert "severity" in payload
