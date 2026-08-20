#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = ROOT.parent
DEFAULT_SOPHIAGRAPH_ROOT = WORKSPACE_ROOT / "sophiagraph"
DEFAULT_TOKENCENSUS_ROOT = WORKSPACE_ROOT / "tokencensus"


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout_seconds: int = 240,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def _require_success(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode == 0:
        return
    raise RuntimeError(
        f"{label} failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _json_stdout(
    result: subprocess.CompletedProcess[str], label: str
) -> dict[str, Any]:
    _require_success(result, label)
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} did not return a JSON object")
    return payload


def _build_wheel(package_root: Path, dist_root: Path, python_bin: Path) -> Path:
    before = {path.name for path in dist_root.glob("*.whl")}
    result = _run(
        [
            str(python_bin),
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(dist_root),
            str(package_root),
        ],
        cwd=WORKSPACE_ROOT,
        env=os.environ.copy(),
        timeout_seconds=600,
    )
    _require_success(result, f"build wheel for {package_root.name}")
    created = sorted(
        path for path in dist_root.glob("*.whl") if path.name not in before
    )
    if not created:
        raise RuntimeError(f"no wheel produced for {package_root}")
    return created[-1]


def _write_minimal_config(home_root: Path) -> None:
    config_path = home_root / ".openminion" / "agents.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "token-pipe": {
                        "name": "token-pipe",
                        "provider": "echo",
                        "default_channel": "console",
                    }
                },
                "default_agent": "token-pipe",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_usage(installed_bin: Path, home_root: Path, data_root: Path) -> str:
    session_id = "token-pipe-session"
    session_db = data_root / "state" / "brain" / "sessions.db"
    common = [
        str(installed_bin / "sessctl"),
        "--home-root",
        str(home_root),
        "--data-root",
        str(data_root),
    ]
    env = _installed_env(home_root, data_root)
    _require_success(
        _run(
            [*common, "init", "--db", str(session_db)],
            cwd=WORKSPACE_ROOT,
            env=env,
        ),
        "sessctl init",
    )
    _require_success(
        _run(
            [
                *common,
                "create-session",
                "--db",
                str(session_db),
                "--session-id",
                session_id,
                "--initial-agent-id",
                "token-pipe",
                "--profile-version",
                "v1",
            ],
            cwd=WORKSPACE_ROOT,
            env=env,
        ),
        "sessctl create-session",
    )
    events = (
        (
            "llm.call.completed",
            {
                "run_id": "run-token-pipe",
                "provider": "echo",
                "model": "echo-test",
                "prompt": "do not export this prompt",
                "api_key": "do-not-export-secret",
                "cost_usd": 0.003,
                "cost_source": "provider",
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 4,
                    "total_tokens": 13,
                    "cache_read_tokens": 2,
                },
            },
        ),
        (
            "llm.call.failed",
            {
                "run_id": "run-token-pipe",
                "provider": "echo",
                "model": "echo-test",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                },
            },
        ),
    )
    for event_type, payload in events:
        _require_success(
            _run(
                [
                    *common,
                    "append-event",
                    "--db",
                    str(session_db),
                    "--session-id",
                    session_id,
                    "--event-type",
                    event_type,
                    "--payload-json",
                    json.dumps(payload, sort_keys=True),
                ],
                cwd=WORKSPACE_ROOT,
                env=env,
            ),
            "sessctl append-event",
        )
    return session_id


def _installed_env(home_root: Path, data_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["OPENMINION_HOME"] = str(home_root)
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    env["OPENMINION_GENERATED_ROOT"] = str(data_root / "runtime")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def _assert_no_source_injection(env: dict[str, str]) -> None:
    value = env.get("PYTHONPATH")
    if value:
        raise RuntimeError(f"PYTHONPATH must be unset for installed proof: {value}")


def _assert_usage_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "openminion.token_usage.v1":
        raise RuntimeError(
            f"unexpected schema_version: {payload.get('schema_version')}"
        )
    if payload.get("session_id") != "token-pipe-session":
        raise RuntimeError("wrong session_id in token usage payload")
    totals = payload.get("totals") or {}
    if totals.get("provider_tokens") != 18:
        raise RuntimeError(f"unexpected provider total: {totals}")
    costs = payload.get("costs") or {}
    if costs.get("provider_cost_usd") != 0.003:
        raise RuntimeError(f"unexpected provider cost: {costs}")
    coverage = payload.get("coverage") or {}
    if coverage.get("failed_llm_call_events") != 1:
        raise RuntimeError(f"unexpected failed-call coverage: {coverage}")
    rendered = json.dumps(payload, sort_keys=True)
    if "do not export" in rendered or "do-not-export-secret" in rendered:
        raise RuntimeError("prompt or secret content leaked into token usage payload")


def _assert_tokencensus_payload(payload: dict[str, Any]) -> None:
    if payload.get("complete") is not True:
        raise RuntimeError(f"TokenCensus did not report complete=true: {payload}")
    if payload.get("envelope_count") != 1 or int(payload.get("record_count") or 0) < 2:
        raise RuntimeError(f"unexpected TokenCensus counts: {payload}")
    totals = payload.get("totals") or {}
    if totals.get("provider_tokens") != 18:
        raise RuntimeError(f"unexpected TokenCensus totals: {totals}")
    rendered = json.dumps(payload, sort_keys=True)
    if "do not export" in rendered or "do-not-export-secret" in rendered:
        raise RuntimeError("prompt or secret content leaked into TokenCensus payload")


def _assert_negative_paths(
    *,
    tokencensus_bin: Path,
    openminion_bin: Path,
    home_root: Path,
    data_root: Path,
    session_id: str,
    usage_payload: dict[str, Any],
) -> None:
    env = _installed_env(home_root, data_root)
    malformed = _run(
        [str(tokencensus_bin), "inspect", "-", "--json"],
        cwd=WORKSPACE_ROOT,
        env=env,
        input_text="{not-json",
    )
    if malformed.returncode == 0:
        raise RuntimeError("TokenCensus accepted malformed JSON")
    newer = dict(usage_payload)
    newer["schema_version"] = "openminion.token_usage.v2"
    newer_result = _run(
        [str(tokencensus_bin), "inspect", "-", "--json"],
        cwd=WORKSPACE_ROOT,
        env=env,
        input_text=json.dumps(newer),
    )
    if newer_result.returncode == 0:
        raise RuntimeError("TokenCensus accepted a newer schema version")
    limited = _json_stdout(
        _run(
            [
                str(openminion_bin),
                "--home-root",
                str(home_root),
                "--data-root",
                str(data_root),
                "--no-interactive",
                "status",
                "tokens",
                "--session-id",
                session_id,
                "--event-limit",
                "1",
                "--json",
            ],
            cwd=WORKSPACE_ROOT,
            env=env,
        ),
        "openminion status tokens limited",
    )
    if limited.get("complete") is not False:
        raise RuntimeError("event-limited token usage payload claimed complete=true")
    limited_census = _json_stdout(
        _run(
            [str(tokencensus_bin), "inspect", "-", "--json"],
            cwd=WORKSPACE_ROOT,
            env=env,
            input_text=json.dumps(limited),
        ),
        "tokencensus inspect limited",
    )
    if limited_census.get("complete") is not False:
        raise RuntimeError("TokenCensus presented incomplete range as complete")


def run_installed_pipe(args: argparse.Namespace) -> dict[str, Any]:
    work_root = (
        Path(args.work_root or tempfile.mkdtemp(prefix="ospr-token-pipe-"))
        .expanduser()
        .resolve()
    )
    dist_root = work_root / "dist"
    venv_root = work_root / "venv"
    home_root = work_root / "home"
    data_root = work_root / "data"
    sophiagraph_root = Path(args.sophiagraph_root).expanduser().resolve()
    tokencensus_root = Path(args.tokencensus_root).expanduser().resolve()
    dist_root.mkdir(parents=True, exist_ok=True)
    python_bin = Path(args.python)
    wheels = [
        _build_wheel(sophiagraph_root, dist_root, python_bin),
        _build_wheel(ROOT, dist_root, python_bin),
        _build_wheel(tokencensus_root, dist_root, python_bin),
    ]
    if venv_root.exists():
        shutil.rmtree(venv_root)
    _require_success(
        _run(
            [str(python_bin), "-m", "venv", str(venv_root)],
            cwd=WORKSPACE_ROOT,
            env=os.environ.copy(),
        ),
        "create installed proof venv",
    )
    installed_python = venv_root / "bin" / "python"
    installed_bin = venv_root / "bin"
    _require_success(
        _run(
            [
                str(installed_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                *[str(path) for path in wheels],
            ],
            cwd=WORKSPACE_ROOT,
            env=os.environ.copy(),
            timeout_seconds=900,
        ),
        "install built wheels",
    )
    env = _installed_env(home_root, data_root)
    _assert_no_source_injection(env)
    _write_minimal_config(home_root)
    session_id = _seed_usage(installed_bin, home_root, data_root)
    usage_result = _run(
        [
            str(installed_bin / "openminion"),
            "--home-root",
            str(home_root),
            "--data-root",
            str(data_root),
            "--no-interactive",
            "status",
            "tokens",
            "--session-id",
            session_id,
            "--json",
        ],
        cwd=WORKSPACE_ROOT,
        env=env,
    )
    usage = _json_stdout(usage_result, "openminion status tokens")
    _assert_usage_payload(usage)
    census = _json_stdout(
        _run(
            [str(installed_bin / "tokencensus"), "inspect", "-", "--json"],
            cwd=WORKSPACE_ROOT,
            env=env,
            input_text=usage_result.stdout,
        ),
        "tokencensus inspect",
    )
    _assert_tokencensus_payload(census)
    _assert_negative_paths(
        tokencensus_bin=installed_bin / "tokencensus",
        openminion_bin=installed_bin / "openminion",
        home_root=home_root,
        data_root=data_root,
        session_id=session_id,
        usage_payload=usage,
    )
    return {
        "ok": True,
        "work_root": str(work_root),
        "wheels": [str(path) for path in wheels],
        "session_id": session_id,
        "provider_tokens": usage["totals"]["provider_tokens"],
        "records": len(usage["records"]),
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the installed OpenMinion-to-TokenCensus pipe proof."
    )
    parser.add_argument("--sophiagraph-root", default=str(DEFAULT_SOPHIAGRAPH_ROOT))
    parser.add_argument("--tokencensus-root", default=str(DEFAULT_TOKENCENSUS_ROOT))
    parser.add_argument("--work-root", default=None)
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        payload = run_installed_pipe(_parse_args(argv))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
