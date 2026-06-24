from __future__ import annotations

import importlib.util
import pathlib
import re
import textwrap

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "validate/public_surface.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_public_surface_under_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validator_runs_advisory_on_current_baseline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    validator = _load_validator()
    exit_code = validator.main()
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out

    detected = validator.scan_reaches(validator._SOURCE_ROOT)
    new, removed = validator.compute_drift(detected, validator._BASELINE_ALLOWLIST)
    if new or removed:
        assert "[ADVISORY] _ADVISORY_MODE=True" in captured.out
    else:
        assert (
            "[OK] detected internal-reach set matches baseline allowlist."
            in captured.out
        )


def test_synthetic_new_internal_reach_is_detected(tmp_path: pathlib.Path) -> None:
    validator = _load_validator()

    source_root = tmp_path
    modules_root = source_root / "modules"
    alpha = modules_root / "alpha"
    beta = modules_root / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)

    (alpha / "__init__.py").write_text("", encoding="utf-8")
    (alpha / "runtime").mkdir()
    (alpha / "runtime" / "__init__.py").write_text("", encoding="utf-8")
    (alpha / "runtime" / "foo.py").write_text("X = 1\n", encoding="utf-8")

    (beta / "__init__.py").write_text("", encoding="utf-8")
    (beta / "caller.py").write_text(
        textwrap.dedent(
            """\
            from openminion.modules.alpha.runtime.foo import X  # noqa: F401
            """
        ),
        encoding="utf-8",
    )

    detected = validator.scan_reaches(source_root)
    assert (
        "modules/beta/caller.py",
        "openminion.modules.alpha.runtime.foo",
    ) in detected, f"expected synthetic reach not detected; got {detected}"

    (beta / "caller_public.py").write_text(
        "from openminion.modules import alpha  # noqa: F401\n",
        encoding="utf-8",
    )
    detected2 = validator.scan_reaches(source_root)
    public_pairs = [r for r in detected2 if r[0] == "modules/beta/caller_public.py"]
    assert not public_pairs, (
        f"package-root import should not surface; got {public_pairs}"
    )

    (alpha / "other.py").write_text(
        "from openminion.modules.alpha.runtime.foo import X  # noqa: F401\n",
        encoding="utf-8",
    )
    detected3 = validator.scan_reaches(source_root)
    self_pairs = [r for r in detected3 if r[0] == "modules/alpha/other.py"]
    assert not self_pairs, f"intra-module reach should not surface; got {self_pairs}"


def test_allowlist_shrink_without_real_removal_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    validator = _load_validator()
    original_baseline = validator._BASELINE_ALLOWLIST
    assert len(original_baseline) >= 1
    shrunk_baseline = tuple(original_baseline[1:])  # drop first reach

    monkeypatch.setattr(validator, "_BASELINE_ALLOWLIST", shrunk_baseline)
    monkeypatch.setattr(validator, "_ADVISORY_MODE", False)

    exit_code = validator.main()
    captured = capsys.readouterr()
    assert exit_code == 1, captured.out
    assert "[DRIFT]" in captured.out
    dropped_importer, dropped_target = original_baseline[0]
    assert dropped_importer in captured.out
    assert dropped_target in captured.out


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
    assert isinstance(validator._FAIL_CATEGORIES, frozenset)
