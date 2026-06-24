from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RECONCILER_PATH = REPO_ROOT / "docs" / "scripts" / "reconcile_tracker_metadata.py"
VALIDATOR_PATH = REPO_ROOT / "docs" / "scripts" / "validate_tracker_docs.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reconciler():
    return _load_module("tmri_reconciler", RECONCILER_PATH)


@pytest.fixture(scope="module")
def validator():
    return _load_module("tmri_validator", VALIDATOR_PATH)


# split_markdown_row: backtick awareness and balanced/unbalanced runs


def test_split_well_formed_row_returns_expected_cells(reconciler):
    line = "| ID | P0 | done | mod | Task | Exit | unit |"
    cells = reconciler.split_markdown_row(line)
    assert cells == ["ID", "P0", "done", "mod", "Task", "Exit", "unit"]


def test_split_pipe_inside_backtick_is_content(reconciler):
    line = (
        "| THU-02 | P0 | done | configs | Replace | Output is empty | "
        '`! rg -n "openminion/.openminion|openminion/.tmp" test-configs` |'
    )
    cells = reconciler.split_markdown_row(line)
    assert len(cells) == 7
    assert cells[2] == "done"


def test_split_unbalanced_triple_backtick_is_literal(reconciler):
    line = (
        "| TUIPOL-03 | task | Fenced code blocks (```) render with colors | "
        "P0 | done | notes |"
    )
    cells = reconciler.split_markdown_row(line)
    assert len(cells) == 6
    assert cells[3] == "P0"
    assert cells[4] == "done"


def test_split_balanced_backtick_run_treated_as_code_span(reconciler):
    line = "| A | ``code with | pipe`` | rest |"
    cells = reconciler.split_markdown_row(line)
    assert len(cells) == 3


# CCRS-shape integrity: malformed extra-column row is rejected


CCRS_SHAPE_ROW = (
    "| CCS-02 | P0 | done |0 | todo | mod | "
    "Add golden parity tests for current trim behavior. | "
    "Golden tests fail on behavior drift. | unit |"
)

CLEAN_HEADER = (
    "| ID | Priority | Status | Module(s) | "
    "Task (implementation detail) | "
    "Exit criteria (validation detail) | Validation scope |"
)
CLEAN_SEP = "| --- | --- | --- | --- | --- | --- | --- |"

CLEAN_TODO_ROW = (
    "| CCS-99 | P0 | todo | mod | Genuinely todo task with enough words. | "
    "Exit criteria with enough words to satisfy validator. | unit |"
)


def _write_synthetic_tracker(
    tmp_path: Path,
    name: str,
    rows: list[str],
    extra_evidence_lines: list[str] | None = None,
) -> Path:
    bucket = tmp_path / "wip"
    bucket.mkdir(parents=True, exist_ok=True)
    target = bucket / f"{name}.md"
    body = [
        f"# {name}",
        "",
        "Original report: 2026-05-04",
        "Last updated: 2026-05-04",
        "Overall status: `todo`",
        "Overall completion: 0% (0/0 done).",
        "",
        "## Dependencies",
        "",
        "1. none",
        "",
        "## Why this tracker exists",
        "",
        "Synthetic TMRI fixture.",
        "",
        "## Task board",
        "",
        CLEAN_HEADER,
        CLEAN_SEP,
        *rows,
        "",
        "## Mandatory Execution Protocol",
        "",
        "1. Synthetic.",
        "",
        "## Agent Execution Instructions",
        "",
        "1. Synthetic.",
        "",
        "## Validation Evidence Log",
        "",
        "| Date | Task ID | Implementation evidence | Validation commands | Result | Owner |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if extra_evidence_lines:
        body.extend(extra_evidence_lines)
    body.extend(
        [
            "",
            "## Change log",
            "",
            "- 2026-05-04: Synthetic fixture.",
            "",
        ]
    )
    target.write_text("\n".join(body) + "\n", encoding="utf-8")
    return target


def test_ccrs_shape_malformed_row_is_not_counted(reconciler, tmp_path):
    tracker = _write_synthetic_tracker(
        tmp_path,
        "synthetic-ccrs-shape",
        [CCRS_SHAPE_ROW, CLEAN_TODO_ROW],
    )
    result = reconciler.reconcile_file(tracker, write=False, touch_last_updated=False)
    # The malformed row is not counted as `done`.
    assert result.counts["done"] == 0
    assert result.counts["todo"] == 1
    # A warning is emitted explaining the shape mismatch.
    assert any("9 cells, expected 7" in w for w in result.malformed_row_warnings)


def test_well_formed_rows_have_no_malformed_warning(reconciler, tmp_path):
    tracker = _write_synthetic_tracker(
        tmp_path,
        "synthetic-clean",
        [
            CLEAN_TODO_ROW,
            (
                "| CCS-100 | P0 | done | mod | "
                "Task with `pipe|inside|backticks` content. | "
                "Exit criteria with enough words. | unit |"
            ),
        ],
    )
    result = reconciler.reconcile_file(tracker, write=False, touch_last_updated=False)
    assert result.counts["done"] == 1
    assert result.counts["todo"] == 1
    assert result.malformed_row_warnings == []


# DP4-shape integrity: status drift from evidence


def test_dp4_shape_status_drift_is_flagged(validator, tmp_path):
    evidence_rows = [
        (
            "| 2026-05-04 | DRIFT-01 | "
            "Work was actually done; evidence proves it. | "
            "`echo done` | pass | agent |"
        ),
    ]
    todo_with_evidence_row = (
        "| DRIFT-01 | P0 | todo | mod | "
        "Status drift: row says todo but evidence row references it. | "
        "Exit criteria with enough words. | unit |"
    )
    todo_without_evidence_row = (
        "| DRIFT-02 | P0 | todo | mod | "
        "Genuinely todo with no evidence row. | "
        "Exit criteria with enough words. | unit |"
    )
    tracker = _write_synthetic_tracker(
        tmp_path,
        "synthetic-dp4-shape",
        [todo_with_evidence_row, todo_without_evidence_row],
        extra_evidence_lines=evidence_rows,
    )
    errors, warnings = validator.validate_file(tracker)
    drift_warnings = [
        w for w in warnings if "DRIFT-01" in w and "drifted from evidence" in w
    ]
    assert drift_warnings, f"expected DRIFT-01 status-drift warning, got: {warnings!r}"
    assert not any("DRIFT-02" in w and "drifted from evidence" in w for w in warnings)


# Validator row-width check (strict mode emits warning, not error)


def test_validator_flags_ccrs_shape_row_width(validator, tmp_path):
    tracker = _write_synthetic_tracker(
        tmp_path,
        "synthetic-validator-ccrs",
        [CCRS_SHAPE_ROW, CLEAN_TODO_ROW],
    )
    errors, warnings = validator.validate_file(tracker)
    width_warnings = [w for w in warnings if "9 cells, expected 7" in w]
    assert width_warnings, f"expected row-width-mismatch warning, got: {warnings!r}"
