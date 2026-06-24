from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _openminion_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_env_flag() -> None:
    if str(os.getenv("PINCHTAB_E2E", "")).strip() != "1":
        pytest.skip("PINCHTAB_E2E=1 not set; skipping real browser E2E.")


def _run_cli(args: list[str], *, allow_failure: bool = False) -> dict:
    root = _openminion_root()
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(root / "src"))
    python = env.get("OPENMINION_PYTHON", sys.executable)
    config = env.get(
        "OPENMINION_E2E_CONFIG",
        str(root / "test-configs" / "per-agent-openrouter-minimax.json"),
    )

    cmd = [python, "-m", "openminion", "--config", config, *args]
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 and not allow_failure:
        raise AssertionError(
            "CLI command failed\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    output = proc.stdout or proc.stderr
    try:
        return _extract_json(output)
    except AssertionError:
        if allow_failure:
            return {"ok": False, "error": "no_json", "raw": output}
        raise


def _extract_json(output: str) -> dict:
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            payload = "\n".join(lines[idx:])
            return json.loads(payload)
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].lstrip().startswith("{"):
            payload = "\n".join(lines[idx:])
            return json.loads(payload)
    raise AssertionError(f"No JSON payload found in output:\n{output}")


def _extract_instance_id(payload: dict) -> str | None:
    tool_data = payload.get("tool", {}).get("data", {})
    if isinstance(tool_data, dict):
        instance = tool_data.get("instance")
        if isinstance(instance, dict):
            value = instance.get("id")
            if isinstance(value, str) and value.strip():
                return value
        nested = tool_data.get("data")
        if isinstance(nested, dict):
            raw = nested.get("raw")
            if isinstance(raw, dict):
                value = raw.get("instance_id") or raw.get("id")
                if isinstance(value, str) and value.strip():
                    return value
    return None


def _extract_tab_ids(payload: dict) -> list[str]:
    tool_data = payload.get("tool", {}).get("data", {})
    if not isinstance(tool_data, dict):
        return []
    rows = tool_data.get("tabs")
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tab_id = str(row.get("id", "")).strip()
        if tab_id:
            out.append(tab_id)
    return out


def test_pinchtab_real_instance_lifecycle_flow() -> None:
    _require_env_flag()

    _run_cli(["sidecar", "--json", "start", "pinchtab", "--yes"])

    instance_payload = _run_cli(
        [
            "tools",
            "run",
            "browser.pinchtab.instance_start",
            "--json",
            '{"mode":"headless"}',
        ]
    )
    instance_id = _extract_instance_id(instance_payload)
    assert instance_id, f"instance_start missing id: {instance_payload}"

    tabs_payload = _run_cli(
        [
            "tools",
            "run",
            "browser.tab.list",
            "--json",
            json.dumps({"instance_id": instance_id}),
        ]
    )
    tab_ids = _extract_tab_ids(tabs_payload)
    assert tab_ids, f"tab_list returned no tabs: {tabs_payload}"

    _run_cli(
        [
            "tools",
            "run",
            "browser.pinchtab.instance_stop",
            "--json",
            json.dumps({"instance_id": instance_id}),
        ]
    )
