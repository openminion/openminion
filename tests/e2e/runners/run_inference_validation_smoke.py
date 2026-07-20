#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class CheckResult:
    name: str
    ok: bool
    details: str


def _run(
    cmd: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout_seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd),
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def _last_json_blob(text: str) -> dict[str, object] | None:
    start_positions = [m.start() for m in re.finditer(r"\{", text)]
    fallback: dict[str, object] | None = None
    for start in reversed(start_positions):
        depth = 0
        end = None
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        if end is None:
            continue
        candidate = text[start:end].strip()
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            if "body" in value or "metadata" in value:
                return value
            if fallback is None:
                fallback = value
    return fallback


def check_import_smoke(py_bin: Path, repo_root: Path) -> CheckResult:
    cmd = [
        str(py_bin),
        "-c",
        (
            "mods=['openminion.tools.exec','openminion.tools.file','openminion.tools.gws',"
            "'openminion.tools.reaction','openminion.tools.search.providers.brave',"
            "'openminion.tools.search.providers.tavily','openminion.tools.weather.providers.openmeteo',"
            "'openminion.modules.retrieve','openminion.modules.brain.meta'];"
            "missing=[];"
            "from importlib.util import find_spec;"
            "[(missing.append(m) if find_spec(m) is None else None) for m in mods];"
            "print('missing=' + ','.join(missing))"
        ),
    ]
    proc = _run(cmd, cwd=repo_root, env=os.environ.copy(), timeout_seconds=30)
    output = (proc.stdout + proc.stderr).strip()
    missing_match = re.search(r"missing=(.*)", output)
    missing = missing_match.group(1).strip() if missing_match else ""
    ok = proc.returncode == 0 and missing == ""
    details = output if output else "no output"
    return CheckResult("import_smoke", ok, details)


def check_chat_turn(
    py_bin: Path,
    openminion_dir: Path,
    config_path: Path,
    agent: str,
    session_id: str,
) -> CheckResult:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(openminion_dir / "src")

    cmd = [
        str(py_bin),
        "-m",
        "openminion",
        "--config",
        str(config_path),
        "--agent",
        agent,
        "--session",
        session_id,
        "--verbosity",
        "quiet",
        "--progress",
        "off",
    ]
    proc = _run(
        cmd, cwd=openminion_dir, env=env, input_text="hi\n/exit\n", timeout_seconds=180
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    failed_turn = "[chat] turn failed:" in combined
    has_ready = "chat ready agent=" in combined
    has_agent_output = f"|{agent}] {agent}:" in combined or f"] {agent}:" in combined
    ok = proc.returncode == 0 and has_ready and (not failed_turn) and has_agent_output
    details = (
        f"returncode={proc.returncode}; has_ready={has_ready}; "
        f"failed_turn={failed_turn}; has_agent_output={has_agent_output}"
    )
    return CheckResult("chat_single_turn", ok, details)


def check_gateway_turn(
    py_bin: Path,
    openminion_dir: Path,
    config_path: Path,
    agent: str,
    session_id: str,
) -> CheckResult:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(openminion_dir / "src")

    cmd = [
        str(py_bin),
        "-m",
        "openminion",
        "--config",
        str(config_path),
        "gateway",
        "run",
        "--once",
        "--agent-id",
        agent,
        "--session-id",
        session_id,
        "--message",
        "hello",
        "--json",
        "--verbosity",
        "quiet",
        "--progress",
        "off",
    ]
    proc = _run(cmd, cwd=openminion_dir, env=env, timeout_seconds=120)
    combined = (proc.stdout or "") + (proc.stderr or "")
    payload = _last_json_blob(combined)
    has_body = isinstance(payload, dict) and bool(str(payload.get("body", "")).strip())
    has_metadata = isinstance(payload, dict) and isinstance(
        payload.get("metadata"), dict
    )
    failed_turn = "turn failed" in combined.lower()
    ok = (
        proc.returncode == 0
        and payload is not None
        and has_body
        and has_metadata
        and (not failed_turn)
    )
    details = (
        f"returncode={proc.returncode}; json_payload={payload is not None}; "
        f"has_body={has_body}; has_metadata={has_metadata}; failed_turn={failed_turn}"
    )
    return CheckResult("gateway_single_turn", ok, details)


def check_retrieve_debug(
    py_bin: Path,
    openminion_dir: Path,
    config_path: Path,
    repo_root: Path,
) -> CheckResult:
    env = os.environ.copy()
    # Include both openminion and openminion-retrieve in PYTHONPATH
    pythonpath_parts = [
        str(openminion_dir / "src"),
        str(repo_root / "openminion-retrieve" / "src"),
    ]
    env["PYTHONPATH"] = ":".join(pythonpath_parts)

    cmd = [
        str(py_bin),
        "-m",
        "openminion",
        "--config",
        str(config_path),
        "debug",
        "modules",
        "--json",
    ]
    proc = _run(cmd, cwd=openminion_dir, env=env, timeout_seconds=30)
    combined = (proc.stdout or "") + (proc.stderr or "")

    # Parse JSON output to find retrieve module
    retrieve_ok = False
    retrieve_status = "not_found"
    retrieve_wiring = "unknown"

    # Find the first JSON object in the output (skip log lines)
    json_start = combined.find("{")
    if json_start != -1:
        json_text = combined[json_start:]
        # Find the matching closing brace for the first object
        depth = 0
        json_end = None
        for i, char in enumerate(json_text):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break

        if json_end:
            try:
                payload = json.loads(json_text[:json_end])
                modules = payload.get("modules", [])
                for mod in modules:
                    if mod.get("module") == "openminion-retrieve":
                        retrieve_status = mod.get("status", "unknown")
                        retrieve_wiring = mod.get("wiring_source", "unknown")
                        retrieve_ok = (
                            retrieve_status == "ok" and retrieve_wiring == "real"
                        )
                        break
            except Exception as exc:
                retrieve_status = f"parse_error: {exc}"

    ok = proc.returncode == 0 and retrieve_ok
    details = (
        f"returncode={proc.returncode}; retrieve_status={retrieve_status}; "
        f"retrieve_wiring={retrieve_wiring}; retrieve_ok={retrieve_ok}"
    )
    return CheckResult("retrieve_debug", ok, details)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inference behavior smoke validator (chat + gateway)."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="OpenMinion config path.",
    )
    parser.add_argument(
        "--agent",
        default="cortensor35",
        help="Agent id to validate for endpoint behavior (for local-only use echo-agent).",
    )
    parser.add_argument(
        "--python-bin",
        default=None,
        help="Python binary to execute OpenMinion.",
    )
    parser.add_argument(
        "--session-prefix",
        default="inference-smoke",
        help="Session prefix for generated chat/gateway checks.",
    )
    args = parser.parse_args()

    framework_root = Path(__file__).resolve().parents[4]
    openminion_dir = Path(
        os.environ.get("OPENMINION_HOME", "") or framework_root / "openminion"
    ).resolve()
    config_path = (
        Path(args.config)
        if args.config
        else (framework_root / ".tmp" / "per-agent.json")
    )
    py_bin = (
        Path(args.python_bin)
        if args.python_bin
        else (openminion_dir / ".venv" / "bin" / "python3.11")
    )

    os.environ.setdefault("OPENMINION_HOME", str(openminion_dir))

    if not py_bin.exists():
        print(f"FAIL: python binary not found: {py_bin}")
        return 1
    if not config_path.exists():
        print(f"FAIL: config file not found: {config_path}")
        return 1

    checks = [
        check_import_smoke(py_bin, openminion_dir),
        check_retrieve_debug(py_bin, openminion_dir, config_path, framework_root),
        check_chat_turn(
            py_bin,
            openminion_dir,
            config_path,
            args.agent,
            f"{args.session_prefix}-chat",
        ),
        check_gateway_turn(
            py_bin,
            openminion_dir,
            config_path,
            args.agent,
            f"{args.session_prefix}-gateway",
        ),
    ]

    print("Inference Validation Smoke Summary")
    print("=================================")
    failures = 0
    for result in checks:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status}: {result.name} :: {result.details}")
        if not result.ok:
            failures += 1

    if failures:
        print(f"\nResult: FAIL ({failures}/{len(checks)} checks failed)")
        return 1
    print(f"\nResult: PASS ({len(checks)}/{len(checks)} checks passed)")
    return 0
