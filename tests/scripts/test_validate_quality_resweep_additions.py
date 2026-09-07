from __future__ import annotations

from pathlib import Path

from scripts.validate import artifact_locations, broad_exception, method_loc
from scripts.validate.passthrough import scan as scan_pass_through


def test_artifact_guard_rejects_root_scratch_file(tmp_path: Path) -> None:
    (tmp_path / "openminion-source-file-list-2026-07-02.txt").write_text("x\n")

    findings = artifact_locations.validate(tmp_path)

    assert [finding.path for finding in findings] == [
        "openminion-source-file-list-2026-07-02.txt"
    ]


def test_artifact_guard_ignores_workspace_tmp_scratch_file(tmp_path: Path) -> None:
    scratch = tmp_path / "workspace-tmp" / "lane"
    scratch.mkdir(parents=True)
    (scratch / "openminion-source-file-list-2026-07-02.txt").write_text("x\n")

    assert artifact_locations.validate(tmp_path) == []


def test_method_loc_baseline_rejects_growth(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    body = "\n".join("    x = 1" for _ in range(101))
    source.write_text(f"def oversized():\n{body}\n", encoding="utf-8")
    baseline = tmp_path / "method.tsv"
    baseline.write_text(
        "# path\tqualname\tloc\treason\n"
        "src/openminion/sample.py\toversized\t101\ttest baseline\n",
        encoding="utf-8",
    )

    findings, _metrics = method_loc.validate(
        repo_root=tmp_path,
        source_root=source_root,
        baseline_path=baseline,
        ceiling=100,
    )

    assert findings == [
        "baselined_method_grew: src/openminion/sample.py:oversized has 102 LOC > baseline 101"
    ]


def test_method_loc_emits_only_a_lower_existing_baseline(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    body = "\n".join("    x = 1" for _ in range(100))
    source.write_text(f"def oversized():\n{body}\n", encoding="utf-8")
    baseline = tmp_path / "method.tsv"
    baseline.write_text(
        "# path\tqualname\tloc\treason\n"
        "src/openminion/sample.py\toversized\t110\ttest baseline\n",
        encoding="utf-8",
    )

    written, findings = method_loc.emit_baseline(
        repo_root=tmp_path,
        source_root=source_root,
        baseline_path=baseline,
        ceiling=100,
    )

    assert written is True
    assert findings == []
    assert "\t101\ttest baseline" in baseline.read_text(encoding="utf-8")


def test_method_loc_rejects_new_over_ceiling_owner(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    body = "\n".join("    x = 1" for _ in range(101))
    source.write_text(f"def oversized():\n{body}\n", encoding="utf-8")
    baseline = tmp_path / "method.tsv"
    baseline.write_text("# path\tqualname\tloc\treason\n", encoding="utf-8")

    written, findings = method_loc.emit_baseline(
        repo_root=tmp_path,
        source_root=source_root,
        baseline_path=baseline,
        ceiling=100,
    )

    assert written is False
    assert findings == ["new_over_ceiling_method: src/openminion/sample.py:oversized"]


def test_broad_exception_rejects_growth(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    source.write_text(
        "def f():\n    try:\n        return 1\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "broad.tsv"
    baseline.write_text(
        f"# path\ttotal\tsilent_pass\treason\n{source.resolve().as_posix()}\t0\t0\ttest baseline\n",
        encoding="utf-8",
    )

    findings, _metrics = broad_exception.validate(
        root=source_root,
        baseline_path=baseline,
    )

    assert findings == [
        f"broad_exception_count_grew: {source.resolve().as_posix()} has 1 > baseline 0",
        f"silent_pass_count_grew: {source.resolve().as_posix()} has 1 > baseline 0",
    ]


def test_broad_exception_emits_componentwise_decrease(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    source.write_text(
        "def f():\n    try:\n        return 1\n    except Exception:\n        return 2\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "broad.tsv"
    baseline.write_text(
        f"# path\ttotal\tsilent_pass\treason\n{source.resolve().as_posix()}\t2\t0\ttest baseline\n",
        encoding="utf-8",
    )

    written, findings = broad_exception.emit_baseline(
        root=source_root,
        baseline_path=baseline,
    )

    assert written is True
    assert findings == []
    assert "\t1\t0\ttest baseline" in baseline.read_text(encoding="utf-8")


def test_broad_exception_rejects_new_owner_without_writing(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    source.write_text(
        "def f():\n    try:\n        return 1\n    except Exception:\n        return 2\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "broad.tsv"
    baseline.write_text(
        "# path\ttotal\tsilent_pass\treason\n",
        encoding="utf-8",
    )
    before = baseline.read_bytes()

    written, findings = broad_exception.emit_baseline(
        root=source_root,
        baseline_path=baseline,
    )

    assert written is False
    assert findings == [f"new_broad_exception_file: {source.resolve().as_posix()}"]
    assert baseline.read_bytes() == before


def test_passthrough_finds_simple_forwarder(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "openminion"
    source_root.mkdir(parents=True)
    source = source_root / "sample.py"
    source.write_text(
        "def target(value):\n"
        "    return value\n\n"
        "def wrapper(value):\n"
        "    return target(value)\n",
        encoding="utf-8",
    )

    findings = scan_pass_through(source_root)

    assert any(finding.function == "wrapper" for finding in findings)
