from pathlib import Path

from scripts.validate import data_root_defaults


def _configure_validator(monkeypatch, tmp_path: Path) -> Path:
    modules_dir = tmp_path / "src" / "openminion" / "modules"
    tests_dir = tmp_path / "tests"
    modules_dir.mkdir(parents=True)
    tests_dir.mkdir()
    monkeypatch.setattr(data_root_defaults, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(data_root_defaults, "MODULES_DIR", modules_dir)
    monkeypatch.setattr(data_root_defaults, "TESTS_DIR", tests_dir)
    monkeypatch.setattr(
        data_root_defaults,
        "TEST_RUNTIME_ROOT_OWNERS",
        ("tests/runtime_runner.py",),
    )
    return tests_dir


def test_test_runtime_root_validation_accepts_complete_roots(
    monkeypatch, tmp_path: Path
) -> None:
    tests_dir = _configure_validator(monkeypatch, tmp_path)
    (tests_dir / "conftest.py").write_text(
        'monkeypatch.setenv("OPENMINION_HOME", "tmp")\n'
        'monkeypatch.setenv("OPENMINION_DATA_ROOT", "tmp/data")\n'
        'monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)\n',
        encoding="utf-8",
    )
    (tests_dir / "runtime_runner.py").write_text(
        "\n".join(data_root_defaults.TEST_ROOT_ENV_NAMES),
        encoding="utf-8",
    )

    assert data_root_defaults.main() == 0


def test_test_runtime_root_validation_rejects_incomplete_or_cwd_storage(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    tests_dir = _configure_validator(monkeypatch, tmp_path)
    (tests_dir / "conftest.py").write_text(
        'monkeypatch.setenv("OPENMINION_HOME", "tmp")\n',
        encoding="utf-8",
    )
    (tests_dir / "runtime_runner.py").write_text(
        "OPENMINION_HOME\nOPENMINION_DATA_ROOT\n"
        'env.setdefault("OPENMINION_DATA_ROOT", "ambient")\n'
        "database_path = Path.cwd()\n",
        encoding="utf-8",
    )

    assert data_root_defaults.main() == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "missing isolated OPENMINION_DATA_ROOT fixture" in output
    assert "generated root must derive from isolated data" in output
    assert "missing explicit OPENMINION_GENERATED_ROOT runtime root" in output
    assert "inherits ambient OPENMINION_DATA_ROOT" in output
    assert "cwd-backed database_path" in output
