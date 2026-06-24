from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TRIAGE_PATH = REPO_ROOT / "docs" / "scripts" / "triage_qa_trackers.py"


def _load_triage_module():
    spec = importlib.util.spec_from_file_location("triage_qa_trackers", TRIAGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load triage script from {TRIAGE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["triage_qa_trackers"] = module
    spec.loader.exec_module(module)
    return module


def _write_tracker(
    path: Path,
    *,
    status: str = "`done`",
    completion: str = "100% (4/4 done)",
    evidence_rows: list[str],
) -> None:
    lines = [
        "# Example Tracker",
        "",
        "Original report: 2026-06-01",
        "Last updated: 2026-06-03",
        "Scope: test fixture",
        f"Overall status: {status}",
        f"Overall completion: {completion}",
        "",
        "## Validation Evidence Log",
        "",
        "| Date | Task ID | Implementation evidence | Validation commands | Result | Owner |",
        "| --- | --- | --- | --- | --- | --- |",
        *evidence_rows,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_classify_requires_explicit_qa_pass_from_different_owner(
    tmp_path: Path,
) -> None:
    triage = _load_triage_module()
    tracker = tmp_path / "tracker.md"
    _write_tracker(
        tracker,
        evidence_rows=[
            "| 2026-06-03 | IMPL-01 | Landed implementation. | `pytest -q` | pass | codex |",
            "| 2026-06-04 | QA | Independent QA review verified live behavior. | `pytest -q` | pass | claude |",
        ],
    )

    result = triage.classify(tracker)

    assert result.classification == triage.PROMOTE
    assert "QA-pass" in result.reason


def test_classify_keeps_self_remediation_done_tracker_out_of_done(
    tmp_path: Path,
) -> None:
    triage = _load_triage_module()
    tracker = tmp_path / "tracker.md"
    _write_tracker(
        tracker,
        evidence_rows=[
            "| 2026-06-03 | IMPL-01 | Landed implementation. | `pytest -q` | pass | codex |",
            "| 2026-06-04 | SELF-REMEDIATION | This is not independent QA evidence. | `pytest -q` | pass | codex |",
        ],
    )

    result = triage.classify(tracker)

    assert result.classification == triage.DRIFT
    assert "missing QA-pass evidence" in result.reason


def test_classify_allows_explicit_self_qa_override(tmp_path: Path) -> None:
    triage = _load_triage_module()
    tracker = tmp_path / "tracker.md"
    _write_tracker(
        tracker,
        evidence_rows=[
            "| 2026-06-03 | IMPL-01 | Landed implementation. | `pytest -q` | pass | codex |",
            "| 2026-06-04 | QA | User-authorized self-QA pass (explicit override of the normal different-agent QA norm). | `pytest -q` | pass | codex |",
        ],
    )

    result = triage.classify(tracker)

    assert result.classification == triage.PROMOTE
