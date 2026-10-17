from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.package_integration


OPENMINION_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = Path(
    os.environ.get("OPENMINION_DOCS_ROOT", OPENMINION_ROOT.parent / "docs")
).expanduser()
RECONCILER_PATH = DOCS_ROOT / "scripts" / "reconcile_tracker_metadata.py"
VALIDATOR_PATH = DOCS_ROOT / "scripts" / "validate_tracker_docs.py"
CLOSEOUT_PATH = DOCS_ROOT / "scripts" / "validate_tracker_closeout.py"
REVIEW_PATH = DOCS_ROOT / "scripts" / "review_wip_trackers.py"


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


@pytest.fixture(scope="module")
def closeout():
    return _load_module("tmri_closeout", CLOSEOUT_PATH)


@pytest.fixture(scope="module")
def review():
    return _load_module("tmri_review", REVIEW_PATH)


def test_paired_docs_scripts_load_with_matching_row_parser(reconciler, validator):
    line = "| A | `code with | pipe` | rest |"
    expected = ["A", "`code with | pipe`", "rest"]
    assert reconciler.split_markdown_row(line) == expected
    assert validator.split_markdown_row(line) == expected


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
    *,
    header: str = CLEAN_HEADER,
    overall_status: str = "todo",
    overall_completion: str = "0% (0/0 done).",
    extra_sections: list[str] | None = None,
) -> Path:
    bucket = tmp_path / "wip"
    bucket.mkdir(parents=True, exist_ok=True)
    target = bucket / f"{name}.md"
    body = [
        f"# {name}",
        "",
        "Original report: 2026-05-04",
        "Last updated: 2026-05-04",
        f"Overall status: `{overall_status}`",
        f"Overall completion: {overall_completion}",
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
        header,
        CLEAN_SEP,
        *rows,
        "",
        *(extra_sections or []),
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
    assert result.counts["done"] == 0
    assert result.counts["todo"] == 1
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


@pytest.mark.parametrize(
    ("task_label", "exit_label"),
    [
        ("Task (implementation detail)", "Exit criteria (validation detail)"),
        ("Task", "Exit criteria"),
        ("Task description", "Evidence / acceptance criteria"),
    ],
)
def test_task_header_aliases_are_recognized(
    reconciler,
    validator,
    tmp_path,
    task_label,
    exit_label,
):
    header = (
        f"| ID | Priority | Status | Module(s) | {task_label} | "
        f"{exit_label} | Validation scope |"
    )
    tracker = _write_synthetic_tracker(
        tmp_path,
        f"synthetic-{task_label.lower().replace(' ', '-')}",
        [CLEAN_TODO_ROW],
        header=header,
        overall_completion="0% (0/1 done).",
    )

    result = reconciler.reconcile_file(
        tracker,
        write=False,
        touch_last_updated=False,
    )
    errors, _warnings = validator.validate_file(tracker)

    assert result.counts["todo"] == 1
    assert not any(
        "no recognized task table" in warning
        for warning in result.malformed_row_warnings
    )
    assert not any("no recognized task table" in error for error in errors)


def test_task_board_without_recognized_table_reports_observed_labels(
    reconciler,
    validator,
    tmp_path,
):
    header = (
        "| ID | Priority | Status | Module(s) | Work item | Completion proof | "
        "Validation scope |"
    )
    tracker = _write_synthetic_tracker(
        tmp_path,
        "synthetic-unsupported-header",
        [CLEAN_TODO_ROW],
        header=header,
    )

    result = reconciler.reconcile_file(
        tracker,
        write=False,
        touch_last_updated=False,
    )
    errors, _warnings = validator.validate_file(tracker)
    observed = (
        "ID | Priority | Status | Module(s) | Work item | Completion proof | "
        "Validation scope"
    )

    assert result.total_tasks == 0
    assert any(
        "Task board has no recognized task table" in warning and observed in warning
        for warning in result.malformed_row_warnings
    )
    assert any(
        "Task board` but no recognized task table" in error and observed in error
        for error in errors
    )


MATCHING_PROGRESS_SUMMARY = [
    "## Progress summary",
    "",
    "| Priority | Done | In Progress | Todo | Blocked | Total |",
    "| --- | --- | --- | --- | --- | --- |",
    "| P0 | 1 | 0 | 0 | 0 | 1 |",
    "| P1 | 0 | 0 | 1 | 0 | 1 |",
    "| **Total** | **1** | **0** | **1** | **0** | **2** |",
    "",
]


def _progress_tracker(tmp_path: Path, *, summary: list[str]) -> Path:
    rows = [
        (
            "| TLVF-01 | P0 | done | scripts | "
            "Verify canonical tracker parsing with current labels. | "
            "Exact focused package integration test passes cleanly. | unit |"
        ),
        (
            "| TLVF-02 | P1 | todo | scripts | "
            "Verify progress summary counts against task rows. | "
            "Mismatched status and total counts fail visibly. | unit |"
        ),
    ]
    return _write_synthetic_tracker(
        tmp_path,
        "synthetic-progress-summary",
        rows,
        overall_status="in_progress",
        overall_completion="50% (1/2 done).",
        extra_sections=summary,
    )


def test_matching_progress_summary_including_total_passes(
    reconciler,
    validator,
    tmp_path,
):
    tracker = _progress_tracker(tmp_path, summary=MATCHING_PROGRESS_SUMMARY)

    result = reconciler.reconcile_file(
        tracker,
        write=True,
        touch_last_updated=False,
    )
    errors, _warnings = validator.validate_file(tracker)

    assert result.changed is False
    assert not any("Progress summary" in error for error in errors)


def test_mismatched_progress_summary_is_reported_and_reconciled(
    reconciler,
    validator,
    tmp_path,
):
    mismatched = [
        "## Progress summary",
        "",
        "| Priority | Done | In Progress | Todo | Blocked | Total |",
        "| --- | --- | --- | --- | --- | --- |",
        "| P0 | 0 | 0 | 1 | 0 | 2 |",
        "| P1 | 0 | 0 | 1 | 0 | 1 |",
        "| **Total** | **0** | **0** | **2** | **0** | **3** |",
        "",
    ]
    tracker = _progress_tracker(tmp_path, summary=mismatched)

    errors, _warnings = validator.validate_file(tracker)
    result = reconciler.reconcile_file(
        tracker,
        write=True,
        touch_last_updated=False,
    )
    reconciled_errors, _warnings = validator.validate_file(tracker)

    assert any("Progress summary P0 done=0" in error for error in errors)
    assert any("Progress summary P0 total=2" in error for error in errors)
    assert any("Progress summary TOTAL total=3" in error for error in errors)
    assert result.changed is True
    assert not any("Progress summary" in error for error in reconciled_errors)


def test_overall_metadata_mismatch_is_reported(validator, tmp_path):
    tracker = _progress_tracker(tmp_path, summary=MATCHING_PROGRESS_SUMMARY)
    text = tracker.read_text(encoding="utf-8")
    tracker.write_text(
        text.replace("Overall status: `in_progress`", "Overall status: `done`").replace(
            "Overall completion: 50% (1/2 done).",
            "Overall completion: 100% (2/2 done).",
        ),
        encoding="utf-8",
    )

    errors, _warnings = validator.validate_file(tracker)

    assert "Overall status 'done' does not match task board 'in_progress'" in errors
    assert any("Overall completion does not match task board" in error for error in errors)


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


def test_validator_flags_ccrs_shape_row_width(validator, tmp_path):
    tracker = _write_synthetic_tracker(
        tmp_path,
        "synthetic-validator-ccrs",
        [CCRS_SHAPE_ROW, CLEAN_TODO_ROW],
    )
    errors, warnings = validator.validate_file(tracker)
    width_warnings = [w for w in warnings if "9 cells, expected 7" in w]
    assert width_warnings, f"expected row-width-mismatch warning, got: {warnings!r}"


def test_qa_closeout_includes_qa_tracker_validation(
    closeout,
    tmp_path,
    monkeypatch,
):
    tracker_root = tmp_path / "trackers"
    tracker_root.mkdir()
    commands: list[list[str]] = []

    def record_step(_label, command, *, cwd):
        del cwd
        commands.append(command)
        return 0

    monkeypatch.setattr(closeout, "_run_step", record_step)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_tracker_closeout.py",
            "--bucket",
            "qa",
            "--tracker-root",
            str(tracker_root),
            "--skip-metadata-reconcile",
            "--skip-env-guard",
        ],
    )

    assert closeout.main() == 0
    assert len(commands) == 1
    assert "--include-qa" in commands[0]


def test_review_helper_rejects_direct_done_destination(review, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["review_wip_trackers.py", "--destination", "done"],
    )

    with pytest.raises(SystemExit) as exc_info:
        review.main()

    assert exc_info.value.code == 2
