from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate import test_skip_budget


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "tests" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _baseline(tmp_path: Path, rows: list[tuple[str, int, int, str]]) -> Path:
    path = tmp_path / "baseline.tsv"
    lines = [test_skip_budget.BASELINE_HEADER]
    lines.extend(
        f"{rel}\t{total}\t{uncond}\t{reason}" for rel, total, uncond, reason in rows
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _validate(tmp_path: Path, baseline: Path) -> tuple[list[str], dict[str, int]]:
    return test_skip_budget.validate(
        tests_root=tmp_path / "tests", baseline_path=baseline
    )


def _sample_test(tmp_path: Path) -> Path:
    return _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.skipif(not _HAS_GIT, reason='git is required')\n"
        "def test_one() -> None:\n"
        "    pass\n"
        "\n"
        "@pytest.mark.skip(reason='needs network')\n"
        "def test_two() -> None:\n"
        "    pass\n",
    )


def _counts(tmp_path: Path) -> test_skip_budget.MarkerCounts:
    return test_skip_budget.iter_marker_counts(tmp_path / "tests")[0]


def test_matching_baseline_is_clean(tmp_path: Path) -> None:
    _sample_test(tmp_path)
    baseline = _baseline(tmp_path, [("test_sample.py", 2, 1, "git is required")])

    findings, metrics = _validate(tmp_path, baseline)

    assert findings == []
    assert metrics["markers_total"] == 2
    assert metrics["unconditional_total"] == 1


def test_unbaselined_markers_are_rejected(tmp_path: Path) -> None:
    _sample_test(tmp_path)
    baseline = _baseline(tmp_path, [])

    findings, _metrics = _validate(tmp_path, baseline)

    assert findings == ["unbaselined_skip_markers: test_sample.py has 2 marker(s)"]


def test_growth_beyond_baseline_is_rejected(tmp_path: Path) -> None:
    _sample_test(tmp_path)
    baseline = _baseline(tmp_path, [("test_sample.py", 1, 0, "git is required")])

    findings, _metrics = _validate(tmp_path, baseline)

    assert any(f.startswith("skip_budget_grew: test_sample.py") for f in findings)
    assert any(
        f.startswith("unconditional_skip_grew: test_sample.py") for f in findings
    )


def test_swapping_conditional_for_unconditional_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.skip(reason='now always skipped')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )
    baseline = _baseline(tmp_path, [("test_sample.py", 1, 0, "git is required")])

    findings, _metrics = _validate(tmp_path, baseline)

    assert findings == [
        "unconditional_skip_grew: test_sample.py has 1 unconditional > baseline 0"
    ]


def test_skipif_literal_true_is_unconditional(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.skipif(True, reason='always skipped')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )

    row = _counts(tmp_path)

    assert (row.total, row.unconditional) == (1, 1)


def test_skipif_literal_false_stays_conditional(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.skipif(False, reason='never skipped')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )

    row = _counts(tmp_path)

    assert (row.total, row.unconditional) == (1, 0)


def test_literal_skipif_upgrade_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.skipif(True, reason='always skipped')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )
    baseline = _baseline(tmp_path, [("test_sample.py", 1, 0, "never skipped")])

    findings, _metrics = _validate(tmp_path, baseline)

    assert findings == [
        "unconditional_skip_grew: test_sample.py has 1 unconditional > baseline 0"
    ]


def test_bare_xfail_is_unconditional(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.xfail(strict=True, reason='known gap')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )

    row = _counts(tmp_path)

    assert (row.total, row.unconditional) == (1, 1)


def test_xfail_condition_false_stays_conditional(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.xfail(condition=False, reason='known gap')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )

    row = _counts(tmp_path)

    assert (row.total, row.unconditional) == (1, 0)


def test_xfail_run_false_without_condition_is_unconditional(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.xfail(run=False, reason='never executed')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )

    row = _counts(tmp_path)

    assert (row.total, row.unconditional) == (1, 1)


def test_annotated_pytestmark_is_counted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_module.py",
        "import pytest\n"
        "\n"
        'pytestmark: list[object] = [pytest.mark.skip(reason="probe")]\n'
        "\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )
    baseline = _baseline(tmp_path, [("test_module.py", 1, 1, "module marker")])

    findings, metrics = _validate(tmp_path, baseline)

    assert findings == []
    assert metrics["markers_total"] == 1


def test_param_marks_are_counted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.parametrize(\n"
        "    'value',\n"
        "    [\n"
        "        pytest.param(1, marks=pytest.mark.skip(reason='needs network')),\n"
        "        pytest.param(2, marks=[pytest.mark.skipif(not _CI, reason='CI only')]),\n"
        "    ],\n"
        ")\n"
        "def test_one(value: int) -> None:\n"
        "    pass\n",
    )

    row = _counts(tmp_path)

    assert (row.total, row.unconditional) == (2, 1)


@pytest.mark.parametrize(
    ("before", "after", "before_unconditional", "after_unconditional"),
    [
        (
            "@pytest.mark.xfail(False, run=False, reason='gap')",
            "@pytest.mark.xfail(True, run=False, reason='gap')",
            0,
            1,
        ),
        (
            "@pytest.mark.skipif(False, False, reason='gap')",
            "@pytest.mark.skipif(False, True, reason='gap')",
            0,
            1,
        ),
        (
            "@pytest.mark.skipif(False, reason='gap')",
            "@pytest.mark.skipif()",
            0,
            1,
        ),
        (
            '@pytest.mark.skipif("False", reason="gap")',
            '@pytest.mark.skipif("True", reason="gap")',
            0,
            1,
        ),
        (
            "@pytest.mark.xfail(False, False, reason='gap')",
            "@pytest.mark.xfail(False, True, reason='gap')",
            0,
            1,
        ),
        (
            "@pytest.mark.skipif(False, reason='gap')",
            "@pytest.mark.skipif",
            0,
            1,
        ),
    ],
)
def test_paired_literal_forms_change_the_unconditional_budget(
    tmp_path: Path,
    before: str,
    after: str,
    before_unconditional: int,
    after_unconditional: int,
) -> None:
    body = "import pytest\n\n{decorator}\ndef test_one() -> None:\n    pass\n"

    _write(tmp_path, "test_sample.py", body.format(decorator=before))
    assert _counts(tmp_path).unconditional == before_unconditional

    _write(tmp_path, "test_sample.py", body.format(decorator=after))
    assert _counts(tmp_path).unconditional == after_unconditional


def test_removed_marker_leaves_stale_headroom(tmp_path: Path) -> None:
    _write(tmp_path, "test_sample.py", "def test_one() -> None:\n    pass\n")
    baseline = _baseline(tmp_path, [("test_sample.py", 2, 1, "git is required")])

    findings, _metrics = _validate(tmp_path, baseline)

    assert findings == [
        "stale_skip_baseline: test_sample.py has no markers; run --emit-baseline"
    ]


def test_deleted_tests_file_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "test_other.py", "def test_one() -> None:\n    pass\n")
    baseline = _baseline(tmp_path, [("test_gone.py", 1, 0, "git is required")])

    findings, _metrics = _validate(tmp_path, baseline)

    assert findings == ["missing_baselined_tests_file: test_gone.py"]


def test_pytestmark_module_markers_are_counted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_module.py",
        "import pytest\n"
        "\n"
        "pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not _CI, reason='CI only')]\n"
        "\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )
    baseline = _baseline(tmp_path, [("test_module.py", 1, 0, "CI only")])

    findings, metrics = _validate(tmp_path, baseline)

    assert findings == []
    assert metrics["files_checked"] == 1


def test_scan_error_is_reported_not_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "test_broken.py", "def test_one(:\n")
    baseline = _baseline(tmp_path, [])

    findings, metrics = _validate(tmp_path, baseline)

    assert findings == ["scan_error: test_broken.py (syntax error at line 1)"]
    assert metrics["markers_total"] == 0


def test_scan_error_blocks_baseline_writes(tmp_path: Path) -> None:
    _write(tmp_path, "test_broken.py", "def test_one(:\n")
    baseline = _baseline(tmp_path, [("test_broken.py", 1, 0, "known marker")])
    before = baseline.read_bytes()

    written, findings = test_skip_budget.emit_baseline(
        tests_root=tmp_path / "tests", baseline_path=baseline
    )

    assert written is False
    assert findings == ["scan_error: test_broken.py (syntax error at line 1)"]
    assert baseline.read_bytes() == before


def test_baseline_requires_a_reason(tmp_path: Path) -> None:
    _sample_test(tmp_path)
    baseline = _baseline(tmp_path, [("test_sample.py", 2, 1, "")])

    with pytest.raises(SystemExit, match="needs a reason"):
        _validate(tmp_path, baseline)


def test_emit_baseline_lowers_headroom(tmp_path: Path) -> None:
    _write(tmp_path, "test_sample.py", "def test_one() -> None:\n    pass\n")
    baseline = _baseline(tmp_path, [("test_sample.py", 2, 1, "git is required")])

    written, findings = test_skip_budget.emit_baseline(
        tests_root=tmp_path / "tests", baseline_path=baseline
    )

    assert written is True
    assert findings == []
    content = baseline.read_text(encoding="utf-8")
    assert "test_sample.py" not in content
    assert content.startswith(test_skip_budget.BASELINE_HEADER)


def test_emit_baseline_keeps_the_authored_reason(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest\n"
        "\n"
        "@pytest.mark.skipif(not _GIT, reason='git is required')\n"
        "def test_one() -> None:\n"
        "    pass\n",
    )
    baseline = _baseline(tmp_path, [("test_sample.py", 2, 0, "authored reason")])

    written, _findings = test_skip_budget.emit_baseline(
        tests_root=tmp_path / "tests", baseline_path=baseline
    )

    assert written is True
    assert "\tauthored reason" in baseline.read_text(encoding="utf-8")


def test_emit_baseline_refuses_unbaselined_markers(tmp_path: Path) -> None:
    _sample_test(tmp_path)
    baseline = _baseline(tmp_path, [])

    written, findings = test_skip_budget.emit_baseline(
        tests_root=tmp_path / "tests", baseline_path=baseline
    )

    assert written is False
    assert findings == ["unbaselined_skip_markers: test_sample.py has 2 marker(s)"]
