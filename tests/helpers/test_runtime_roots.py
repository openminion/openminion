from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from tests.helpers.runtime_roots import configure_runtime_roots, isolate_runtime_roots


OPENMINION_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_SPILL_NAMES = {
    ".openminion",
    "artifact",
    "artifacts",
    "arts",
    "authored_tools",
    "backup",
    "ci",
    "cli",
    "config",
    "identity",
    "logs",
    "memory",
    "policy",
    "reference",
    "retrieve",
    "session-context",
    "skill",
    "state",
    "storage",
    "task_summary",
    "tool-runs",
    "wc",
}


def test_configure_runtime_roots_uses_one_home_family(tmp_path: Path) -> None:
    environment: dict[str, str] = {}

    generated_root = configure_runtime_roots(tmp_path, environment)

    assert Path(environment["OPENMINION_HOME"]) == tmp_path
    assert Path(environment["OPENMINION_DATA_ROOT"]) == tmp_path / ".openminion"
    assert Path(environment["OPENMINION_GENERATED_ROOT"]) == generated_root


def test_python_helper_replaces_ambient_roots(monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_HOME", "/unsafe/home")
    monkeypatch.setenv("OPENMINION_DATA_ROOT", "/unsafe/data")
    monkeypatch.setenv("OPENMINION_GENERATED_ROOT", "/unsafe/generated")

    generated_root = isolate_runtime_roots(prefix="openminion-python-roots-")
    home_root = Path(os.environ["OPENMINION_HOME"])

    assert home_root != Path("/unsafe/home")
    assert Path(os.environ["OPENMINION_DATA_ROOT"]) == home_root / ".openminion"
    assert Path(os.environ["OPENMINION_GENERATED_ROOT"]) == generated_root
    assert generated_root == home_root / ".openminion" / "runtime"


def test_python_helper_can_isolate_child_environment() -> None:
    environment = {
        "OPENMINION_HOME": "/unsafe/home",
        "OPENMINION_DATA_ROOT": "/unsafe/data",
    }

    generated_root = isolate_runtime_roots(
        environment, prefix="openminion-child-roots-"
    )
    home_root = Path(environment["OPENMINION_HOME"])

    assert Path(environment["OPENMINION_DATA_ROOT"]) == home_root / ".openminion"
    assert Path(environment["OPENMINION_GENERATED_ROOT"]) == generated_root


def test_shell_helper_replaces_ambient_roots() -> None:
    helper = Path(__file__).with_name("runtime_roots.sh")
    script = f"""
source {helper!s}
isolate_openminion_test_roots openminion-shell-roots
printf '%s\\n' "$OPENMINION_HOME" "$OPENMINION_DATA_ROOT" "$OPENMINION_GENERATED_ROOT"
"""
    environment = dict(os.environ)
    environment["OPENMINION_HOME"] = "/unsafe/home"
    environment["OPENMINION_DATA_ROOT"] = "/unsafe/data"

    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    home_root, data_root, generated_root = map(Path, result.stdout.splitlines())

    assert home_root != Path("/unsafe/home")
    assert data_root == home_root / ".openminion"
    assert generated_root == data_root / "runtime"


def test_pytest_collection_does_not_write_to_caller_workspace(tmp_path: Path) -> None:
    caller_workspace = tmp_path / "caller-workspace"
    caller_workspace.mkdir()
    environment = dict(os.environ)
    for name in (
        "OPENMINION_HOME",
        "OPENMINION_DATA_ROOT",
        "OPENMINION_GENERATED_ROOT",
    ):
        environment.pop(name, None)
    python_path = [str(OPENMINION_ROOT), str(OPENMINION_ROOT / "src")]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    representative_tests = [
        OPENMINION_ROOT / "tests" / "artifact" / "test_config.py",
        OPENMINION_ROOT / "tests" / "identity" / "test_config_helpers.py",
        OPENMINION_ROOT / "tests" / "memory" / "test_config.py",
        OPENMINION_ROOT / "tests" / "policy" / "test_policy_service.py",
        OPENMINION_ROOT / "tests" / "retrieve" / "test_scope_keys.py",
        OPENMINION_ROOT / "tests" / "session" / "test_session_store_construction.py",
        OPENMINION_ROOT / "tests" / "skill" / "test_skill.py",
        OPENMINION_ROOT / "tests" / "storage" / "test_engine_config_pool_fields.py",
    ]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *(str(path) for path in representative_tests),
        ],
        cwd=caller_workspace,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not WORKSPACE_SPILL_NAMES.intersection(
        path.name for path in caller_workspace.iterdir()
    )
