from __future__ import annotations

from pathlib import Path

from scripts.validate import execution_complexity


def _execution_root(tmp_path: Path) -> Path:
    root = tmp_path / "src/openminion/services/agent/execution"
    root.mkdir(parents=True)
    (root / "executor.py").write_text(
        "class TurnExecutor:\n"
        "    def run_required_tool_lane(self, value):\n"
        "        result = value\n"
        "        return result\n\n"
        "    async def handle_unforced_tool_calls(self, value):\n"
        "        result = value\n"
        "        return result\n",
        encoding="utf-8",
    )
    return root


def _baseline(
    tmp_path: Path,
    *,
    package_loc: int = 10_000,
    passthrough_count: int = 0,
    dispatch_owner_count: int = 1,
) -> Path:
    path = tmp_path / "baseline.tsv"
    path.write_text(
        f"package_loc\t{package_loc}\n"
        f"passthrough_count\t{passthrough_count}\n"
        f"dispatch_owner_count\t{dispatch_owner_count}\n",
        encoding="utf-8",
    )
    return path


def _findings(tmp_path: Path, root: Path, baseline: Path):
    findings, _metrics = execution_complexity.validate(
        repo_root=tmp_path,
        execution_root=root,
        baseline_path=baseline,
    )
    return findings


def test_validator_accepts_bounded_execution_package(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)

    assert _findings(tmp_path, root, _baseline(tmp_path)) == []


def test_validator_rejects_package_loc_growth(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)
    current_loc = sum(len(path.read_text().splitlines()) for path in root.rglob("*.py"))

    findings = _findings(
        tmp_path, root, _baseline(tmp_path, package_loc=current_loc - 1)
    )

    assert "package_loc_growth" in {finding.code for finding in findings}


def test_validator_rejects_file_and_method_loc_growth(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)
    (root / "large_file.py").write_text("value = 1\n" * 501, encoding="utf-8")
    body = "\n".join("    value = 1" for _ in range(101))
    (root / "large_method.py").write_text(
        f"def oversized():\n{body}\n", encoding="utf-8"
    )

    findings = _findings(tmp_path, root, _baseline(tmp_path))
    codes = {finding.code for finding in findings}

    assert {"file_loc_limit", "method_loc_limit"} <= codes


def test_validator_rejects_widened_internal_parameters(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)
    (root / "parameters.py").write_text(
        "def _oversized(a, b, c, d, e, f, g, h, i, j, k):\n    return a\n",
        encoding="utf-8",
    )

    findings = _findings(tmp_path, root, _baseline(tmp_path))

    assert "internal_parameter_limit" in {finding.code for finding in findings}


def test_validator_rejects_second_combined_dispatch_owner(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)
    (root / "second_dispatch.py").write_text(
        "class SecondDispatch:\n"
        "    def run_required_tool_lane(self):\n"
        "        return None\n\n"
        "    def handle_unforced_tool_calls(self):\n"
        "        return None\n",
        encoding="utf-8",
    )

    findings = _findings(tmp_path, root, _baseline(tmp_path))

    assert "dispatch_owner_growth" in {finding.code for finding in findings}


def test_validator_rejects_passthrough_growth(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)
    (root / "wrapper.py").write_text(
        "def target(value):\n"
        "    result = value\n"
        "    return result\n\n"
        "def wrapper(value):\n"
        "    return target(value)\n",
        encoding="utf-8",
    )

    findings = _findings(tmp_path, root, _baseline(tmp_path))

    assert "passthrough_growth" in {finding.code for finding in findings}


def test_validator_rejects_duplicate_helpers(tmp_path: Path) -> None:
    root = _execution_root(tmp_path)
    (root / "first.py").write_text(
        "def _shared_helper(value):\n    return value\n", encoding="utf-8"
    )
    (root / "second.py").write_text(
        "def _shared_helper(value):\n    return value\n", encoding="utf-8"
    )

    findings = _findings(tmp_path, root, _baseline(tmp_path))

    assert "duplicate_helpers" in {finding.code for finding in findings}
