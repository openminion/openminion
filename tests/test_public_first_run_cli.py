from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "openminion", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )


def _extract_doctor_payload(output: str) -> dict[str, object]:
    match = re.search(r"(\{\s*\"checks\":.*\})\s*\Z", output, re.S)
    if match is None:
        raise AssertionError(f"doctor JSON payload not found in output:\n{output}")
    return json.loads(match.group(1))


def test_first_user_cli_path_succeeds_with_echo_provider(tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    data_root = home_root / ".openminion"
    config_path = tmp_path / "config.json"
    home_root.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["OPENMINION_HOME"] = str(home_root)
    env["OPENMINION_DATA_ROOT"] = str(data_root)

    config_init = _run_cli(
        "--config",
        str(config_path),
        "config",
        "init",
        "--provider",
        "echo",
        "--force",
        env=env,
    )
    assert config_init.returncode == 0, config_init.stderr or config_init.stdout

    verify_smoke = _run_cli(
        "--config",
        str(config_path),
        "verify",
        "smoke",
        env=env,
    )
    assert verify_smoke.returncode == 0, verify_smoke.stdout + verify_smoke.stderr
    assert "verify: OK suite=smoke" in verify_smoke.stdout

    doctor = _run_cli(
        "--config",
        str(config_path),
        "doctor",
        "--json",
        env=env,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    doctor_payload = _extract_doctor_payload(doctor.stdout)
    assert doctor_payload["summary"]["ok"] is True
    assert doctor_payload["summary"]["default_channel"] == "console"

    agent = _run_cli(
        "--config",
        str(config_path),
        "agent",
        "--message",
        "hello",
        env=env,
    )
    assert agent.returncode == 0, agent.stdout + agent.stderr
    assert "openminion: hello" in agent.stdout
