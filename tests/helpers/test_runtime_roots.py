from __future__ import annotations

import os
from pathlib import Path
import subprocess

from tests.helpers.runtime_roots import isolate_runtime_roots


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
