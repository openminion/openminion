from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate/filename_underscore_hygiene.py"
)


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load_module("validate_filename_underscore_hygiene")


def test_validate_filename_underscore_hygiene_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0
    assert captured["ok"] is True
    assert captured["validator"] == "validate_filename_underscore_hygiene"


def test_scan_python_files_ignores_dunder_files(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "turn_intent.py").write_text("", encoding="utf-8")
    (src_root / "too_many_words_here.py").write_text("", encoding="utf-8")

    detected, scanned_files = MODULE.scan_python_files(
        scan_roots=(src_root,),
        repo_root=tmp_path,
    )

    assert scanned_files == 3
    assert detected == [("src/too_many_words_here.py", 3)]


def test_strict_mode_fails_on_new_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "good.py").write_text("", encoding="utf-8")
    (src_root / "new_name_with_three_parts.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(MODULE, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(MODULE, "_SCAN_ROOTS", (src_root,))
    monkeypatch.setattr(MODULE, "_BASELINE_ALLOWLIST", ())
    monkeypatch.setattr(
        MODULE,
        "_BASELINE_ALLOWLIST_PATH",
        tmp_path / "scripts" / "baselines" / "filename_underscore_hygiene.tsv",
    )

    rc = MODULE.main(["--strict"])
    captured = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert rc == 1
    assert captured["ok"] is False
    assert captured["metrics"]["new_entries"] == 1


def test_strict_mode_reports_tests_only_without_failing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    src_root = tmp_path / "src"
    tests_root = tmp_path / "tests"
    src_root.mkdir()
    tests_root.mkdir()
    (tests_root / "test_name_with_three_parts.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(MODULE, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(MODULE, "_SCAN_ROOTS", (src_root, tests_root))
    monkeypatch.setattr(MODULE, "_BASELINE_ALLOWLIST", ())
    monkeypatch.setattr(
        MODULE,
        "_BASELINE_ALLOWLIST_PATH",
        tmp_path / "scripts" / "baselines" / "filename_underscore_hygiene.tsv",
    )

    rc = MODULE.main(["--strict"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "Tests inventory" in stderr
    assert "1 test filename(s)" in stderr
    assert "--show-tests-detail" in stderr
    assert "test_name_with_three_parts.py" not in stderr


def test_show_tests_detail_lists_test_paths(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    src_root = tmp_path / "src"
    tests_root = tmp_path / "tests"
    src_root.mkdir()
    tests_root.mkdir()
    (tests_root / "test_name_with_three_parts.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(MODULE, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(MODULE, "_SCAN_ROOTS", (src_root, tests_root))
    monkeypatch.setattr(MODULE, "_BASELINE_ALLOWLIST", ())
    monkeypatch.setattr(
        MODULE,
        "_BASELINE_ALLOWLIST_PATH",
        tmp_path / "scripts" / "baselines" / "filename_underscore_hygiene.tsv",
    )

    rc = MODULE.main(["--strict", "--show-tests-detail"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "test_name_with_three_parts.py" in stderr


def test_write_baseline_honors_cli_argv(tmp_path: Path, monkeypatch) -> None:
    src_root = tmp_path / "src"
    scripts_root = tmp_path / "scripts"
    src_root.mkdir()
    scripts_root.mkdir()
    (src_root / "three_word_name.py").write_text("", encoding="utf-8")

    baseline_path = scripts_root / "baselines" / "filename_underscore_hygiene.tsv"
    monkeypatch.setattr(MODULE, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(MODULE, "_SCAN_ROOTS", (src_root,))
    monkeypatch.setattr(MODULE, "_BASELINE_ALLOWLIST", ())
    monkeypatch.setattr(MODULE, "_BASELINE_ALLOWLIST_PATH", baseline_path)

    rc = MODULE.main(["--write-baseline"])

    assert rc == 0
    assert baseline_path.read_text(encoding="utf-8") == "src/three_word_name.py\t2\n"
