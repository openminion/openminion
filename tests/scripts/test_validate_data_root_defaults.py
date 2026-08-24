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
    helpers_dir = tests_dir / "helpers"
    helpers_dir.mkdir()
    (helpers_dir / "runtime_roots.py").write_text(
        "tempfile.mkdtemp\n" + "\n".join(data_root_defaults.TEST_ROOT_ENV_NAMES),
        encoding="utf-8",
    )
    (helpers_dir / "runtime_roots.sh").write_text(
        "mktemp -d\n" + "\n".join(data_root_defaults.TEST_ROOT_ENV_NAMES),
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    monkeypatch.setattr(data_root_defaults, "PYTHON_RUNNER_ROOT_EXEMPTIONS", {})
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
    runners_dir = tests_dir / "e2e" / "runners"
    runners_dir.mkdir(parents=True)
    (runners_dir / "run_managed.py").write_text(
        "isolate_runtime_roots(prefix='test-')\n",
        encoding="utf-8",
    )
    (runners_dir / "run_managed.sh").write_text(
        "source ../../helpers/runtime_roots.sh\n"
        "isolate_openminion_test_roots test-runner\n",
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
    runners_dir = tests_dir / "e2e" / "runners"
    runners_dir.mkdir(parents=True)
    (runners_dir / "run_unmanaged.py").write_text(
        "database_path = Path.cwd()\n",
        encoding="utf-8",
    )
    (runners_dir / "run_unmanaged.sh").write_text(
        'CONFIG_PATH="${OPENMINION_DIR}/test-configs/generated.json"\n'
        'ARTIFACT_ROOT="${TMPDIR:-/tmp}/openminion-test-artifacts"\n'
        "openminion --config test-configs/missing.json\n"
        "pytest\n",
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        "OPENMINION_HOME ?= $(REPO_ROOT)\n"
        "OPENMINION_DATA_ROOT ?= $(OPENMINION_HOME)/.openminion\n",
        encoding="utf-8",
    )

    assert data_root_defaults.main() == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "missing isolated OPENMINION_DATA_ROOT fixture" in output
    assert "generated root must derive from isolated data" in output
    assert "unmanaged executable test runtime roots" in output
    assert "missing shared shell runtime-root helper" in output
    assert "unmanaged executable shell runtime roots" in output
    assert "package-checkout config write default" in output
    assert "missing package-relative test config" in output
    assert "shared temporary artifact default" in output
    assert "test home defaults to the package checkout" in output
    assert "test data defaults to the package checkout" in output
    assert "cwd-backed database_path" in output


def test_test_runtime_root_validation_reports_missing_shared_helper(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    tests_dir = _configure_validator(monkeypatch, tmp_path)
    (tests_dir / "conftest.py").write_text(
        'monkeypatch.setenv("OPENMINION_HOME", "tmp")\n'
        'monkeypatch.setenv("OPENMINION_DATA_ROOT", "tmp/data")\n'
        'monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)\n',
        encoding="utf-8",
    )
    (tests_dir / "helpers" / "runtime_roots.py").unlink()

    assert data_root_defaults.main() == 1
    captured = capsys.readouterr()
    assert "missing shared runtime-root helper" in captured.err


def test_test_runtime_root_validation_rejects_late_runner_isolation(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    tests_dir = _configure_validator(monkeypatch, tmp_path)
    (tests_dir / "conftest.py").write_text(
        'monkeypatch.setenv("OPENMINION_HOME", "tmp")\n'
        'monkeypatch.setenv("OPENMINION_DATA_ROOT", "tmp/data")\n'
        'monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)\n',
        encoding="utf-8",
    )
    runners_dir = tests_dir / "e2e" / "runners"
    runners_dir.mkdir(parents=True)
    (runners_dir / "run_late.py").write_text(
        "from openminion.base.config import ConfigManager\n"
        "isolate_runtime_roots(prefix='late-')\n",
        encoding="utf-8",
    )

    assert data_root_defaults.main() == 1
    captured = capsys.readouterr()
    assert "runtime-root isolation must precede OpenMinion imports" in captured.err


def test_test_runtime_root_validation_rejects_unmanaged_direct_test(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    tests_dir = _configure_validator(monkeypatch, tmp_path)
    (tests_dir / "conftest.py").write_text(
        'monkeypatch.setenv("OPENMINION_HOME", "tmp")\n'
        'monkeypatch.setenv("OPENMINION_DATA_ROOT", "tmp/data")\n'
        'monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)\n',
        encoding="utf-8",
    )
    direct_test = tests_dir / "memory" / "test_direct.py"
    direct_test.parent.mkdir()
    direct_test.write_text(
        "from openminion.base.config import ConfigManager\n"
        "if __name__ == '__main__':\n"
        "    print(ConfigManager)\n",
        encoding="utf-8",
    )

    assert data_root_defaults.main() == 1
    captured = capsys.readouterr()
    assert "unmanaged executable test runtime roots" in captured.err
