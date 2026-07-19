from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import textwrap


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "validate/direct_env_calls.py"
_RULES_PATH = _REPO_ROOT / "scripts" / "baselines" / "env_guard_rules.json"
_PY = _REPO_ROOT / ".venv" / "bin" / "python3.11"


def _load_script_module():
    module_name = "direct_env_calls_under_test"
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_load_rules_parses_canonical_rules_file(tmp_path: pathlib.Path) -> None:
    mod = _load_script_module()
    rules = mod._load_rules(_RULES_PATH)
    assert len(rules) >= 30, (
        f"Canonical rules file should have >=30 rules; got {len(rules)}"
    )
    sample = rules[0]
    assert isinstance(sample.path, pathlib.Path)
    assert isinstance(sample.max_calls, int)
    assert sample.max_calls >= 0
    assert sample.category, "category must be a non-empty string"
    assert sample.reason, "reason must be a non-empty string"


def test_load_rules_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    mod = _load_script_module()
    missing = tmp_path / "does-not-exist.json"
    assert mod._load_rules(missing) == []


def test_load_rules_malformed_json_returns_empty(tmp_path: pathlib.Path) -> None:
    mod = _load_script_module()
    bad = tmp_path / "broken.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    assert mod._load_rules(bad) == []


def test_count_file_finds_direct_calls(tmp_path: pathlib.Path) -> None:
    mod = _load_script_module()
    sample = tmp_path / "sample.py"
    sample.write_text(
        textwrap.dedent(
            """
            import os
            a = os.getenv("X")
            b = os.environ.get("Y")
            c = os.environ["Z"]
            d = os.path.join("a", "b")  # not a match
            """
        ).strip(),
        encoding="utf-8",
    )
    count, lines = mod._count_file(sample)
    assert count == 3
    assert lines == [2, 3, 4]


def test_script_warn_mode_against_canonical_rules_passes() -> None:
    proc = subprocess.run(
        [str(_PY), str(_SCRIPT_PATH), "--warn"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"warn-mode exit={proc.returncode}; stderr={proc.stderr!r}"
    )
    assert "violations=0" in proc.stdout, (
        f"expected violations=0 in stdout; got:\n{proc.stdout}"
    )


def test_script_fail_mode_against_canonical_rules_passes() -> None:
    proc = subprocess.run(
        [str(_PY), str(_SCRIPT_PATH), "--fail-on-violation"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"fail-mode exit={proc.returncode}; stderr={proc.stderr!r}"
    )
    assert "violations=0" in proc.stdout


def test_script_fail_mode_triggers_on_synthetic_violation(
    tmp_path: pathlib.Path,
) -> None:
    target = pathlib.Path("src/openminion/base/logging.py")
    forced = tmp_path / "forced-violation-rules.json"
    forced.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "path": str(target),
                        "max_calls": 0,
                        "category": "runtime-reader",
                        "reason": "synthetic test override (must be violation)",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            str(_PY),
            str(_SCRIPT_PATH),
            "--rules",
            str(forced),
            "--fail-on-violation",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1, (
        f"expected non-zero exit on synthetic violation; got {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "[violation]" in proc.stdout
    assert "[runtime_violation]" in proc.stdout, (
        f"expected runtime_violation category label; stdout:\n{proc.stdout}"
    )
    assert "rule_category=runtime-reader" in proc.stdout


def test_script_warn_lines_include_category_labels() -> None:
    proc = subprocess.run(
        [str(_PY), str(_SCRIPT_PATH), "--warn"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "[boundary_exception]" in proc.stdout, (
        f"expected [boundary_exception] label in warn output; got:\n{proc.stdout}"
    )
    assert "rule_category=canonical-boundary-owner" in proc.stdout, (
        f"expected rule_category tag in warn output; got:\n{proc.stdout}"
    )
