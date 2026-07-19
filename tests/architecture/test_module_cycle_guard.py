from __future__ import annotations

import importlib
import importlib.util
import pathlib
import re
import textwrap

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "validate/module_cycles.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_module_cycles_under_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validator_runs_clean_on_current_baseline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    validator = _load_validator()
    exit_code = validator.main()
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out

    graph = validator.build_module_graph(validator._MODULES_ROOT)
    detected = validator.find_simple_cycles(graph, max_len=validator._MAX_CYCLE_LEN)
    detected_set = set(detected)
    baseline_set = set(validator._BASELINE_ALLOWLIST)

    new = detected_set - baseline_set
    removed = baseline_set - detected_set
    assert not new, f"new cycles surfaced (not in baseline): {sorted(new)}"
    assert not removed, (
        "baseline cycles missing from current detection (auto-shrink the "
        f"_BASELINE_ALLOWLIST in {_SCRIPT_PATH}): {sorted(removed)}"
    )

    assert "[OK] detected cycle set matches baseline allowlist." in captured.out


def test_synthetic_new_cycle_is_detected(tmp_path: pathlib.Path) -> None:
    validator = _load_validator()

    modules_root = tmp_path / "modules"
    alpha = modules_root / "alpha"
    beta = modules_root / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / "__init__.py").write_text(
        textwrap.dedent(
            """\
            from openminion.modules.beta import thing  # noqa: F401
            """
        ),
        encoding="utf-8",
    )
    (beta / "__init__.py").write_text(
        textwrap.dedent(
            """\
            from openminion.modules.alpha import other  # noqa: F401
            thing = "x"
            """
        ),
        encoding="utf-8",
    )
    (beta / "more.py").write_text(
        "from openminion.modules.alpha.deep import sub  # noqa: F401\n",
        encoding="utf-8",
    )

    graph = validator.build_module_graph(modules_root)
    detected = validator.find_simple_cycles(graph, max_len=validator._MAX_CYCLE_LEN)

    assert "alpha" in graph and "beta" in graph
    assert "beta" in graph["alpha"]
    assert "alpha" in graph["beta"]
    assert ("alpha", "beta", "alpha") in detected


def test_allowlist_shrink_without_real_removal_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    validator = _load_validator()
    original_baseline = validator._BASELINE_ALLOWLIST

    synthetic_cycle: tuple[str, ...] = (
        "synthetic_baseline_a",
        "synthetic_baseline_b",
        "synthetic_baseline_a",
    )
    inflated_baseline = original_baseline + (synthetic_cycle,)

    monkeypatch.setattr(validator, "_BASELINE_ALLOWLIST", inflated_baseline)
    monkeypatch.setattr(validator, "_ADVISORY_MODE", False)

    exit_code = validator.main()
    captured = capsys.readouterr()
    assert exit_code == 1, captured.out
    assert "[DRIFT]" in captured.out
    rendered = " -> ".join(synthetic_cycle)
    assert rendered in captured.out


def test_advisory_mode_flag_is_diff_visible() -> None:
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^_ADVISORY_MODE\s*:\s*bool\s*=\s*(True|False)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(source)
    assert match is not None, (
        "_ADVISORY_MODE must be a top-level typed bool constant matching "
        f"the regex {pattern.pattern!r} in {_SCRIPT_PATH}"
    )
    validator = _load_validator()
    assert isinstance(validator._ADVISORY_MODE, bool)
