"""Pure structural analyzer for review-diff tool payloads."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.constants import (
    REVIEW_DIFF_LARGE_DELETION_THRESHOLD as LARGE_DELETION_THRESHOLD,
    REVIEW_DIFF_MANY_FILES_THRESHOLD as MANY_FILES_THRESHOLD,
)


MAX_FINDINGS_RETURNED = 32

_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)\s*$")
_DIFF_PLUSPLUS_RE = re.compile(r"^\+\+\+ b/(?P<path>\S+)\s*$")
_HUNK_HEADER_RE = re.compile(r"^@@ ")
_TEST_PATH_RE = re.compile(r"(?:^|/)(tests?|__tests__|spec)(?:/|_|\.)", re.IGNORECASE)
_TODO_INSERT_RE = re.compile(r"^\+(?!\+\+).*\b(TODO|FIXME|XXX)\b", re.IGNORECASE)


class ReviewFinding(BaseModel):
    """One structured finding from the v1 analyzer."""

    model_config = ConfigDict(extra="forbid")

    kind: Annotated[str, Field(min_length=1, max_length=64)]
    severity: Literal["ok", "warn", "block"] = "warn"
    file: str = ""
    line: int = 0
    message: Annotated[str, Field(max_length=240)] = ""


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: tuple[ReviewFinding, ...] = ()
    file_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    severity: Literal["ok", "warn", "block"] = "ok"
    summary: Annotated[str, Field(max_length=480)] = ""


def analyze_diff(diff_text: str | None) -> ReviewResult:
    text = str(diff_text or "")
    if not text.strip():
        return ReviewResult()
    files = _parse_diff_files(text)
    if not files:
        return ReviewResult()
    findings: list[ReviewFinding] = []
    file_count = len(files)
    total_added = sum(f["added"] for f in files)
    total_removed = sum(f["removed"] for f in files)
    test_paths = [f["path"] for f in files if _is_test_path(f["path"])]
    source_paths = [
        f["path"]
        for f in files
        if not _is_test_path(f["path"]) and not _is_doc_path(f["path"])
    ]
    if source_paths and not test_paths:
        findings.append(
            ReviewFinding(
                kind="no_test_for_new_code",
                severity="warn",
                file=source_paths[0],
                message=(
                    f"{len(source_paths)} source file(s) changed but no "
                    "test file was touched."
                ),
            )
        )
    if file_count > MANY_FILES_THRESHOLD:
        findings.append(
            ReviewFinding(
                kind="many_files_changed",
                severity="warn",
                file=files[0]["path"],
                message=(
                    f"{file_count} files changed in this diff "
                    f"(threshold {MANY_FILES_THRESHOLD})."
                ),
            )
        )
    for file_block in files:
        if file_block["max_hunk_removed"] > LARGE_DELETION_THRESHOLD:
            findings.append(
                ReviewFinding(
                    kind="large_deletion",
                    severity="block",
                    file=file_block["path"],
                    message=(
                        f"Hunk removed {file_block['max_hunk_removed']} lines "
                        f"(threshold {LARGE_DELETION_THRESHOLD})."
                    ),
                )
            )
        for line_no, todo_text in file_block["todo_lines"]:
            findings.append(
                ReviewFinding(
                    kind="todo_or_fixme_introduced",
                    severity="warn",
                    file=file_block["path"],
                    line=line_no,
                    message=todo_text[:200],
                )
            )
    # Cap findings to keep payloads bounded.
    capped = tuple(findings[:MAX_FINDINGS_RETURNED])
    severity = _aggregate_severity(capped)
    summary = (
        f"{file_count} file(s) changed, "
        f"+{total_added}/-{total_removed} lines; "
        f"{len(capped)} finding(s); severity={severity}."
    )
    return ReviewResult(
        findings=capped,
        file_count=file_count,
        lines_added=total_added,
        lines_removed=total_removed,
        severity=severity,
        summary=summary,
    )


def _parse_diff_files(text: str) -> list[dict]:
    lines = text.splitlines()
    files: list[dict] = []
    current: dict | None = None
    current_hunk_removed = 0
    current_added_line_no = 0
    for raw in lines:
        if not raw:
            if current is not None:
                current["max_hunk_removed"] = max(
                    current.get("max_hunk_removed", 0), current_hunk_removed
                )
                current_hunk_removed = 0
            continue
        m_header = _DIFF_FILE_HEADER_RE.match(raw)
        if m_header is not None:
            if current is not None:
                current["max_hunk_removed"] = max(
                    current.get("max_hunk_removed", 0), current_hunk_removed
                )
                files.append(current)
            current = {
                "path": m_header.group("b"),
                "added": 0,
                "removed": 0,
                "max_hunk_removed": 0,
                "todo_lines": [],
            }
            current_hunk_removed = 0
            current_added_line_no = 0
            continue
        m_plus = _DIFF_PLUSPLUS_RE.match(raw)
        if m_plus is not None:
            if current is None:
                current = {
                    "path": m_plus.group("path"),
                    "added": 0,
                    "removed": 0,
                    "max_hunk_removed": 0,
                    "todo_lines": [],
                }
            else:
                pass
            continue
        if _HUNK_HEADER_RE.match(raw):
            if current is not None:
                current["max_hunk_removed"] = max(
                    current.get("max_hunk_removed", 0), current_hunk_removed
                )
            current_hunk_removed = 0
            current_added_line_no = 0
            continue
        if current is None:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            current["added"] += 1
            current_added_line_no += 1
            if _TODO_INSERT_RE.match(raw):
                current["todo_lines"].append((current_added_line_no, raw[1:].strip()))
        elif raw.startswith("-"):
            current["removed"] += 1
            current_hunk_removed += 1
    if current is not None:
        current["max_hunk_removed"] = max(
            current.get("max_hunk_removed", 0), current_hunk_removed
        )
        files.append(current)
    return files


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path or ""))


def _is_doc_path(path: str) -> bool:
    lower = (path or "").lower()
    if lower.endswith((".md", ".rst", ".txt")):
        return True
    if "/docs/" in lower or lower.startswith("docs/"):
        return True
    return False


_SEVERITY_RANK = {"ok": 0, "warn": 1, "block": 2}


def _aggregate_severity(
    findings: tuple[ReviewFinding, ...],
) -> Literal["ok", "warn", "block"]:
    if not findings:
        return "ok"
    rank = max(_SEVERITY_RANK.get(f.severity, 0) for f in findings)
    return ("ok", "warn", "block")[rank]  # type: ignore[return-value]


__all__ = [
    "LARGE_DELETION_THRESHOLD",
    "MANY_FILES_THRESHOLD",
    "MAX_FINDINGS_RETURNED",
    "ReviewFinding",
    "ReviewResult",
    "analyze_diff",
]
