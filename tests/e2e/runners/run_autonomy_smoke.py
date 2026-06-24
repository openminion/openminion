#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


def _now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _slug(raw: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw.lower()
    )


def _parse_json_stdout(stdout_text: str) -> dict[str, Any] | None:
    lines = stdout_text.splitlines()
    starts = [idx for idx, line in enumerate(lines) if line.strip() == "{"]
    for idx in starts:
        candidate = "\n".join(lines[idx:]).strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
) -> tuple[bool, int | None, dict[str, Any] | None, str | None]:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            if not isinstance(payload, dict):
                return False, response.getcode(), None, "response_not_object"
            return True, response.getcode(), payload, None
    except urllib_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - defensive fallback
            detail = str(exc)
        return False, int(exc.code), None, detail
    except Exception as exc:  # pragma: no cover - system/network failures
        return False, None, None, str(exc)


def _http_sse(
    url: str,
    *,
    body: dict[str, Any],
    timeout_seconds: float = 30.0,
) -> tuple[bool, int | None, str, str | None]:
    headers = {"Content-Type": "application/json"}
    req = urllib_request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return True, response.getcode(), raw, None
    except urllib_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - defensive fallback
            detail = str(exc)
        return False, int(exc.code), detail, detail
    except Exception as exc:  # pragma: no cover - system/network failures
        return False, None, "", str(exc)


def _derive_openminion_home(root: Path) -> Path:
    env_home = str(os.environ.get("OPENMINION_HOME", "")).strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    if (root / "openminion").is_dir():
        return root.resolve()
    if root.name == "openminion" and (root.parent / "openminion").is_dir():
        return root.parent.resolve()
    return root.resolve()


class AutonomySmokeSuite:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path(args.root).resolve()
        self.openminion_home = _derive_openminion_home(self.root)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.output_dir = Path(args.output_dir).resolve() / ts
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.output_dir / "config.json"
        self.db_path = self.output_dir / "openminion.db"
        self.session_id = args.session_id
        self.agent_id = args.agent_id
        self.timeout_seconds = float(args.timeout_seconds)
        self.checks: list[dict[str, Any]] = []

        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = "src"
        self.env.setdefault("HOME", str(self.root))
        self.env["OPENMINION_HOME"] = str(self.openminion_home)
        self.env.setdefault(
            "OPENMINION_DATA_ROOT",
            str(self.openminion_home / ".openminion"),
        )

    def _write_text(self, relpath: str, content: str) -> str:
        path = self.output_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return str(path)

    def _write_json(self, relpath: str, payload: dict[str, Any]) -> str:
        return self._write_text(
            relpath, json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )

    def _run_cli(self, check_id: str, command_args: list[str]) -> dict[str, Any]:
        started = time.time()
        full_args = [
            sys.executable,
            "-m",
            "openminion.cli.main",
            "--config",
            str(self.config_path),
            *command_args,
        ]
        completed = subprocess.run(
            full_args,
            cwd=str(self.root),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
        )
        duration_ms = int((time.time() - started) * 1000)
        slug = _slug(check_id)
        stdout_path = self._write_text(
            f"commands/{slug}.stdout.log", completed.stdout or ""
        )
        stderr_path = self._write_text(
            f"commands/{slug}.stderr.log", completed.stderr or ""
        )
        payload = _parse_json_stdout(completed.stdout or "")
        if payload is not None:
            self._write_json(f"commands/{slug}.payload.json", payload)
        return {
            "args": full_args,
            "exit_code": int(completed.returncode),
            "duration_ms": duration_ms,
            "payload": payload,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
        }

    def _record_check(
        self,
        *,
        check_id: str,
        required: bool,
        ok: bool,
        summary: str,
        details: dict[str, Any],
    ) -> None:
        self.checks.append(
            {
                "id": check_id,
                "required": bool(required),
                "ok": bool(ok),
                "summary": summary,
                "details": details,
            }
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {check_id}: {summary}", flush=True)

    def _run_baseline_checks(self) -> None:
        init_result = self._run_cli(
            "config-init",
            [
                "config",
                "init",
                "--provider",
                "echo",
                "--force",
                "--storage-path",
                str(self.db_path),
            ],
        )
        init_ok = init_result["exit_code"] == 0
        self._record_check(
            check_id="config-init",
            required=True,
            ok=init_ok,
            summary="initialize throwaway config",
            details=init_result,
        )

        run_turn = self._run_cli(
            "run-turn",
            [
                "run",
                "Autonomy smoke baseline run.",
                "--session",
                self.session_id,
                "--agent",
                self.agent_id,
                "--json",
            ],
        )
        run_payload = run_turn.get("payload") or {}
        run_id = (
            run_payload.get("trace_id")
            or ((run_payload.get("turn") or {}).get("run_id"))
            or ""
        )
        run_ok = bool(
            run_turn["exit_code"] == 0
            and run_payload.get("ok") is True
            and ((run_payload.get("turn") or {}).get("session_id") == self.session_id)
        )
        self._record_check(
            check_id="run-turn",
            required=True,
            ok=run_ok,
            summary="single-turn run completes and returns JSON payload",
            details={**run_turn, "run_id": run_id},
        )

        run_stream = self._run_cli(
            "run-stream",
            [
                "run",
                "Autonomy smoke stream run.",
                "--session",
                self.session_id,
                "--agent",
                self.agent_id,
                "--stream",
                "--json",
            ],
        )
        stream_payload = run_stream.get("payload") or {}
        stream_ok = bool(
            run_stream["exit_code"] == 0
            and stream_payload.get("ok") is True
            and (
                (stream_payload.get("turn") or {}).get("session_id") == self.session_id
            )
        )
        self._record_check(
            check_id="run-stream",
            required=True,
            ok=stream_ok,
            summary="stream-mode run path completes",
            details=run_stream,
        )

        tool_run = self._run_cli(
            "tool-artifact-ref",
            [
                "tools",
                "run",
                "list_files",
                "--json",
                '{"path":".","depth":1}',
                "--session",
                self.session_id,
            ],
        )
        tool_payload = tool_run.get("payload") or {}
        artifact_refs = tool_payload.get("artifact_refs")
        artifact_ok = bool(
            tool_run["exit_code"] == 0
            and tool_payload.get("ok") is True
            and isinstance(artifact_refs, list)
            and len(artifact_refs) >= 1
        )
        self._record_check(
            check_id="tool-artifact-ref",
            required=True,
            ok=artifact_ok,
            summary="tool execution emits artifact refs",
            details=tool_run,
        )

        status_runs = self._run_cli(
            "status-runs",
            [
                "status",
                "runs",
                "--session-id",
                self.session_id,
                "--json",
            ],
        )
        status_runs_payload = status_runs.get("payload") or {}
        runs = (
            status_runs_payload.get("runs")
            if isinstance(status_runs_payload.get("runs"), list)
            else []
        )
        status_runs_ok = bool(
            status_runs["exit_code"] == 0
            and status_runs_payload.get("ok") is True
            and len(runs) >= 1
        )
        self._record_check(
            check_id="status-runs",
            required=True,
            ok=status_runs_ok,
            summary="session runs are queryable",
            details=status_runs,
        )

        run_id_for_events = run_id or (runs[0].get("run_id") if runs else "")
        status_events = self._run_cli(
            "status-run-events",
            [
                "status",
                "run-events",
                "--session-id",
                self.session_id,
                "--run-id",
                str(run_id_for_events),
                "--json",
            ],
        )
        status_events_payload = status_events.get("payload") or {}
        events = (
            status_events_payload.get("events")
            if isinstance(status_events_payload.get("events"), list)
            else []
        )
        event_types = {
            event.get("event_type")
            for event in events
            if isinstance(event, dict) and isinstance(event.get("event_type"), str)
        }
        events_ok = bool(
            status_events["exit_code"] == 0
            and status_events_payload.get("ok") is True
            and len(events) >= 1
            and "run.completed" in event_types
        )
        self._record_check(
            check_id="status-run-events",
            required=True,
            ok=events_ok,
            summary="run lifecycle events are persisted and queryable",
            details={**status_events, "run_id": run_id_for_events},
        )

    def _wait_for_api_health(self, base_url: str) -> tuple[bool, dict[str, Any]]:
        attempts = max(1, int(self.args.api_startup_retries))
        for attempt in range(1, attempts + 1):
            ok, status, payload, err = _http_json(
                "GET",
                f"{base_url}/v1/health",
                timeout_seconds=float(self.args.api_timeout_seconds),
            )
            if ok and status == 200 and isinstance(payload, dict):
                return True, {
                    "attempt": attempt,
                    "status": status,
                    "payload": payload,
                    "error": None,
                }
            time.sleep(float(self.args.api_retry_sleep_seconds))
        return False, {
            "attempt": attempts,
            "status": status,
            "payload": payload,
            "error": err,
        }

    def _run_api_checks(self) -> None:
        base_url = f"http://{self.args.api_host}:{self.args.api_port}"
        api_log_path = self.output_dir / "api" / "server.log"
        api_log_path.parent.mkdir(parents=True, exist_ok=True)
        with api_log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "openminion.cli.main",
                    "--config",
                    str(self.config_path),
                    "api",
                    "run",
                    "--host",
                    self.args.api_host,
                    "--port",
                    str(self.args.api_port),
                ],
                cwd=str(self.root),
                env=self.env,
                stdout=log_file,
                stderr=log_file,
                text=True,
            )
            try:
                ready, health = self._wait_for_api_health(base_url)
                self._record_check(
                    check_id="api-health",
                    required=bool(self.args.require_api),
                    ok=ready,
                    summary="api server starts and exposes /v1/health",
                    details={
                        **health,
                        "base_url": base_url,
                        "log_path": str(api_log_path),
                    },
                )
                if not ready:
                    if bool(self.args.require_mission_endpoints):
                        self._record_check(
                            check_id="api-mission-surface",
                            required=True,
                            ok=False,
                            summary="mission API surface unavailable because API startup failed",
                            details={
                                "implemented": False,
                                "reason": "api_not_ready",
                                "base_url": base_url,
                            },
                        )
                    return

                stream_ok, stream_status, stream_text, stream_err = _http_sse(
                    f"{base_url}/v1/turn/stream",
                    body={
                        "prompt": "Autonomy smoke SSE check.",
                        "session_id": self.session_id,
                        "agent_id": self.agent_id,
                    },
                    timeout_seconds=float(self.args.api_timeout_seconds),
                )
                stream_path = self._write_text("api/turn-stream.sse.txt", stream_text)
                has_meta = "event: meta" in stream_text
                has_done = "event: done" in stream_text
                self._record_check(
                    check_id="api-turn-stream",
                    required=bool(self.args.require_api),
                    ok=bool(stream_ok and has_meta and has_done),
                    summary="turn stream endpoint returns SSE meta/done events",
                    details={
                        "status": stream_status,
                        "error": stream_err,
                        "has_meta_event": has_meta,
                        "has_done_event": has_done,
                        "sse_path": stream_path,
                    },
                )

                mission_calls: list[tuple[str, str, dict[str, Any] | None]] = [
                    (
                        "POST",
                        "/v1/missions",
                        {
                            "mission_id": "autonomy-smoke",
                            "goal": "smoke mission creation probe",
                        },
                    ),
                    ("GET", "/v1/missions/autonomy-smoke", None),
                    ("POST", "/v1/missions/autonomy-smoke/resume", {}),
                    ("POST", "/v1/missions/autonomy-smoke/cancel", {}),
                ]
                mission_results: list[dict[str, Any]] = []
                for idx, (method, route, body) in enumerate(mission_calls, start=1):
                    ok, status, payload, err = _http_json(
                        method,
                        f"{base_url}{route}",
                        body=body,
                        timeout_seconds=float(self.args.api_timeout_seconds),
                    )
                    mission_results.append(
                        {
                            "call": idx,
                            "method": method,
                            "route": route,
                            "ok": ok,
                            "status": status,
                            "payload": payload,
                            "error": err,
                        }
                    )
                self._write_json("api/mission-probes.json", {"probes": mission_results})
                implemented = all(
                    result.get("status") not in {None, 404, 405, 501}
                    for result in mission_results
                )
                self._record_check(
                    check_id="api-mission-surface",
                    required=bool(self.args.require_mission_endpoints),
                    ok=implemented,
                    summary="mission API surface is available",
                    details={"implemented": implemented, "probes": mission_results},
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def run(self) -> int:
        started_at = _now_utc()
        self._run_baseline_checks()
        if self.args.with_api:
            self._run_api_checks()

        required_checks = [
            check for check in self.checks if check.get("required") is True
        ]
        failed_required = [
            check for check in required_checks if check.get("ok") is not True
        ]
        optional_failures = [
            check
            for check in self.checks
            if check.get("required") is not True and check.get("ok") is not True
        ]

        report = {
            "started_at": started_at,
            "ended_at": _now_utc(),
            "root": str(self.root),
            "openminion_home": str(self.openminion_home),
            "openminion_data_root": str(self.env.get("OPENMINION_DATA_ROOT", "")),
            "config_path": str(self.config_path),
            "db_path": str(self.db_path),
            "output_dir": str(self.output_dir),
            "with_api": bool(self.args.with_api),
            "require_api": bool(self.args.require_api),
            "require_mission_endpoints": bool(self.args.require_mission_endpoints),
            "checks": self.checks,
            "summary": {
                "total_checks": len(self.checks),
                "required_checks": len(required_checks),
                "required_failed": len(failed_required),
                "optional_failed": len(optional_failures),
                "ok": len(failed_required) == 0,
            },
        }
        report_path = self._write_json("report.json", report)
        print(f"Report written: {report_path}", flush=True)
        return 0 if len(failed_required) == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenMinion autonomy smoke suite (baseline + optional API probes).",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[3]),
        help="OpenMinion module root (default: openminion/ root).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output root for smoke artifacts (default: <root>/.tmp/autonomy-smoke).",
    )
    parser.add_argument(
        "--session-id", default="autonomy-smoke", help="Session id for smoke commands."
    )
    parser.add_argument(
        "--agent-id", default="openminion", help="Agent id for smoke commands."
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="CLI command timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--with-api", action="store_true", help="Run API startup and endpoint probes."
    )
    parser.add_argument(
        "--require-api",
        action="store_true",
        help="Treat API startup/stream checks as required when --with-api is used.",
    )
    parser.add_argument(
        "--require-mission-endpoints",
        action="store_true",
        help="Treat /v1/missions* probes as required checks.",
    )
    parser.add_argument(
        "--api-host", default="127.0.0.1", help="API bind host for probe run."
    )
    parser.add_argument(
        "--api-port", type=int, default=8879, help="API bind port for probe run."
    )
    parser.add_argument(
        "--api-timeout-seconds",
        type=float,
        default=20.0,
        help="HTTP request timeout for API probes.",
    )
    parser.add_argument(
        "--api-startup-retries",
        type=int,
        default=30,
        help="Retry count while waiting for /v1/health.",
    )
    parser.add_argument(
        "--api-retry-sleep-seconds",
        type=float,
        default=0.25,
        help="Delay between /v1/health retries.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if bool(args.require_api) and not bool(args.with_api):
        args.with_api = True
    if bool(args.require_mission_endpoints) and not bool(args.with_api):
        args.with_api = True
    if args.output_dir is None:
        args.output_dir = str(Path(args.root).resolve() / ".tmp" / "autonomy-smoke")
    suite = AutonomySmokeSuite(args)
    return suite.run()
