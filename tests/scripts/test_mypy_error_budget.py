from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.validate import mypy_error_budget as budget


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src" / "openminion").mkdir(parents=True)
    (repo / "src" / "openminion" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[tool.mypy]\n", encoding="utf-8")
    return repo


def _write_baseline(path: Path, package_errors: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "package_errors": package_errors,
        "total_errors": sum(package_errors.values()),
        "monthly_burn_down_quota": budget.DEFAULT_MONTHLY_BURN_DOWN_QUOTA,
        "historical_floor": budget.HISTORICAL_FLOOR,
        "reset_debt": {"review_artifact": "", "package_errors": {}, "total_errors": 0},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _stable_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget, "_git_output", lambda *_args: "abc123")
    monkeypatch.setattr(budget, "_mypy_version", lambda _repo: "mypy 1.0")


def test_emit_baseline_accepts_package_and_total_decrease(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = repo / "scripts" / "baselines" / "mypy_baseline.json"
    _write_baseline(baseline, {"modules": 5, "tools": 2})

    result = budget._emit_monotonic_baseline(
        repo_root=repo,
        baseline_path=baseline,
        current={"modules": 4, "tools": 1},
        total=5,
    )

    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["package_errors"] == {"modules": 4, "tools": 1}
    assert payload["total_errors"] == 5
    assert payload["generated_at"].endswith("Z")
    assert payload["source_commit"] == "abc123"
    assert payload["historical_floor"]["total_errors"] == 4019


def test_emit_baseline_rejects_total_increase(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = repo / "scripts" / "baselines" / "mypy_baseline.json"
    _write_baseline(baseline, {"modules": 5})
    before = baseline.read_text(encoding="utf-8")

    result = budget._emit_monotonic_baseline(
        repo_root=repo,
        baseline_path=baseline,
        current={"modules": 6},
        total=6,
    )

    assert result == 1
    assert baseline.read_text(encoding="utf-8") == before


def test_emit_baseline_rejects_package_increase_when_total_falls(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = repo / "scripts" / "baselines" / "mypy_baseline.json"
    _write_baseline(baseline, {"modules": 5, "tools": 5})
    before = baseline.read_text(encoding="utf-8")

    result = budget._emit_monotonic_baseline(
        repo_root=repo,
        baseline_path=baseline,
        current={"modules": 6, "tools": 1},
        total=7,
    )

    assert result == 1
    assert baseline.read_text(encoding="utf-8") == before


def test_read_only_report_includes_floor_and_debt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = budget._print_report(
        current={"modules": 4},
        total=4,
        baseline={
            "package_errors": {"modules": 5},
            "historical_floor": {"total_errors": 4019},
            "reset_debt": {"total_errors": 100},
        },
        lines=[],
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "historical floor total: 4019" in out
    assert "reset debt total: 100" in out


def test_source_snapshot_hash_changes_when_checked_source_changes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    source = repo / "src" / "openminion" / "changed.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    before = budget._source_snapshot_sha256(repo)
    source.write_text("VALUE = 2\n", encoding="utf-8")

    assert budget._source_snapshot_sha256(repo) != before


def test_reviewed_reset_rejects_noncanonical_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = repo / "scripts" / "baselines" / "mypy_baseline.json"
    _write_baseline(baseline, {"modules": 5})
    review = repo / "mypy-reset.json"
    review.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="mypy_resets"):
        budget._load_review(repo_root=repo, baseline_path=baseline, path=review)


def test_reviewed_reset_requires_named_approval(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = repo / "scripts" / "baselines" / "mypy_baseline.json"
    _write_baseline(baseline, {"modules": 5})
    review_dir = repo / "scripts" / "baselines" / "mypy_resets"
    review_dir.mkdir(parents=True)
    review = review_dir / "reset.json"
    metadata = budget._metadata(repo, counts={}, total=0)
    payload = {
        "source_commit": metadata["source_commit"],
        "source_snapshot_sha256": metadata["source_snapshot_sha256"],
        "python_version": metadata["python_version"],
        "mypy_version": metadata["mypy_version"],
        "config_sha256": metadata["config_sha256"],
        "command": metadata["command"],
        "approved_by": "",
        "approval_reference": "review-thread",
        "expires_at": (datetime.now(UTC).replace(microsecond=0) + timedelta(days=1))
        .isoformat()
        .replace("+00:00", "Z"),
        "previous_baseline": {"package_errors": {"modules": 5}, "total_errors": 5},
        "proposed_baseline": {"package_errors": {"modules": 6}, "total_errors": 6},
    }
    review.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="approved_by"):
        budget._load_review(repo_root=repo, baseline_path=baseline, path=review)
