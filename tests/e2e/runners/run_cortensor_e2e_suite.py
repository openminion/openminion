#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote

DEFAULT_CORTENSOR_TEST_API_KEY = "fabc7432-a81e-47a9-a352-31145275809a"


def _now_utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _parse_json_stdout(stdout_text: str) -> Optional[Dict[str, Any]]:
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return None
    return None


def _build_long_block(label: str, repeat: int = 8) -> str:
    unit = (
        f"{label}: maintain strict continuity, keep facts stable across turns, "
        "and preserve previously shared identifiers for later verification. "
    )
    return "".join(unit for _ in range(max(1, int(repeat))))


def _run_subprocess(
    args: List[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    timeout_seconds: int,
) -> Dict[str, Any]:
    started = time.time()
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": None,
            "duration_ms": int((time.time() - started) * 1000),
            "payload": None,
            "stdout": "",
            "stderr": "",
            "error": "subprocess_timeout",
        }

    payload = _parse_json_stdout(completed.stdout or "")
    error_message: Optional[str] = None
    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        stdout_text = (completed.stdout or "").strip()
        snippet = stderr_text or stdout_text or "no_output"
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        error_message = f"subprocess_exit_{completed.returncode}: {snippet}"
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "payload": payload,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "error": error_message,
    }


def _http_json(
    method: str,
    url: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 30,
) -> Tuple[bool, Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    data: Optional[bytes] = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return False, response.getcode(), None, "response_not_object"
            return True, response.getcode(), payload, None
    except urllib_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        return False, int(exc.code), None, detail
    except Exception as exc:  # pragma: no cover - network/system failures
        return False, None, None, str(exc)


def _parse_session_ids(raw: str) -> List[int]:
    parsed: List[int] = []
    for item in [part.strip() for part in str(raw or "").split(",")]:
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            continue
        if value > 0:
            parsed.append(value)
    return parsed


def _unique_positive_ints(values: List[int]) -> List[int]:
    unique: List[int] = []
    seen: set[int] = set()
    for raw_value in values:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        unique.append(parsed)
    return unique


def _build_pythonpath_entries(root: Path, existing_pythonpath: str) -> List[str]:
    entries: List[str] = []

    def _append(path_value: Path) -> None:
        resolved = str(path_value.resolve())
        if resolved not in entries:
            entries.append(resolved)

    root_src = root / "src"
    if root_src.exists():
        _append(root_src)

    script_root = Path(__file__).resolve().parents[3]
    script_src = script_root / "src"
    if script_src.exists():
        _append(script_src)

    monorepo_root = root.parent
    for candidate in sorted(monorepo_root.glob("openminion-*")):
        src_dir = candidate / "src"
        if src_dir.exists():
            _append(src_dir)

    for item in str(existing_pythonpath or "").split(os.pathsep):
        value = item.strip()
        if not value:
            continue
        resolved = str(Path(value).expanduser().resolve())
        if resolved not in entries:
            entries.append(resolved)

    return entries


def _derive_openminion_home(root: Path) -> Path:
    env_home = str(os.environ.get("OPENMINION_HOME", "")).strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    if (root / "openminion").is_dir():
        return (root / "openminion").resolve()
    if root.name == "openminion" and (root.parent / "openminion").is_dir():
        return root.resolve()
    return root.resolve()


class CortensorE2ESuite:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path(args.root).resolve()
        self.openminion_home = _derive_openminion_home(self.root)
        self.python_bin = (
            str(getattr(args, "python_bin", "") or sys.executable).strip()
            or sys.executable
        )
        self.base_config_path = self._resolve_path_from_root(args.base_config)
        fallback_raw = str(getattr(args, "fallback_config", "") or "").strip()
        self.fallback_config_path = (
            self._resolve_path_from_root(fallback_raw) if fallback_raw else None
        )
        self._ensure_base_config_path()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.root)
        self.env["OPENMINION_HOME"] = str(self.openminion_home)
        self.env.setdefault(
            "OPENMINION_DATA_ROOT",
            str(self.openminion_home / ".openminion"),
        )
        self.env["PYTHONPATH"] = os.pathsep.join(
            _build_pythonpath_entries(self.root, str(self.env.get("PYTHONPATH", "")))
        )
        resolved_cortensor_api_key = (
            str(self.env.get("CORTENSOR_API_KEY", "")).strip()
            or DEFAULT_CORTENSOR_TEST_API_KEY
        )
        self.env["CORTENSOR_API_KEY"] = resolved_cortensor_api_key
        self.cortensor_api_key = resolved_cortensor_api_key
        self.results: List[Dict[str, Any]] = []
        dedicated_session_ids = _parse_session_ids(args.dedicated_session_ids)
        ephemeral_session_ids = _parse_session_ids(args.ephemeral_session_ids)
        self.runtime_config = self._build_runtime_config(
            name_suffix="primary",
            session_id=args.session_id,
            session_ids=_parse_session_ids(args.session_ids),
            dedicated_session_ids=dedicated_session_ids,
            ephemeral_session_ids=ephemeral_session_ids,
            session_pool=args.session_pool,
            session_parallel_requests=args.session_parallel_requests,
            session_retry_rounds=args.session_retry_rounds,
        )
        self.failover_config = self._build_runtime_config(
            name_suffix="bad-primary",
            session_id=args.bad_primary_session_id,
            session_ids=[args.session_id, *_parse_session_ids(args.session_ids)],
            dedicated_session_ids=dedicated_session_ids,
            ephemeral_session_ids=ephemeral_session_ids,
            session_pool="mixed",
            # Force sequential candidate handling to guarantee explicit failover path coverage.
            session_parallel_requests=1,
            session_retry_rounds=1,
            # Keep failover validation fast to avoid CLI subprocess timeout.
            timeout_seconds=min(20, int(args.timeout_seconds)),
            transport_timeout_buffer_seconds=min(
                2, int(args.transport_timeout_buffer_seconds)
            ),
            result_wait_attempts=1,
            result_wait_interval_seconds=0.0,
        )
        self._assert_runtime_imports()

    def _assert_runtime_imports(self) -> None:
        result = _run_subprocess(
            [
                self.python_bin,
                "-c",
                (
                    "import openminion\n"
                    "import openminion.modules.brain\n"
                    "import openminion.modules.context\n"
                    "import openminion.modules.session\n"
                    "import openminion.modules.context.compress\n"
                    "print('ok')\n"
                ),
            ],
            cwd=self.root,
            env=self.env,
            timeout_seconds=20,
        )
        if result.get("ok"):
            return
        raise RuntimeError(
            "runtime import preflight failed; verify local package wiring "
            f"(python={self.python_bin}, error={result.get('error')})"
        )

    def _resolve_path_from_root(self, raw_path: str) -> Path:
        candidate = Path(str(raw_path or "").strip()).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.root / candidate).resolve()

    def _ensure_base_config_path(self) -> None:
        if self.base_config_path.exists():
            return
        if self.fallback_config_path is not None and self.fallback_config_path.exists():
            self.base_config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.fallback_config_path, self.base_config_path)
            print(
                "CONFIG_FALLBACK",
                json.dumps(
                    {
                        "copied_from": str(self.fallback_config_path),
                        "copied_to": str(self.base_config_path),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return
        fallback_text = (
            str(self.fallback_config_path)
            if self.fallback_config_path is not None
            else "(disabled)"
        )
        raise FileNotFoundError(
            f"base config not found: {self.base_config_path} (fallback={fallback_text})"
        )

    def _build_runtime_config(
        self,
        *,
        name_suffix: str,
        session_id: int,
        session_ids: List[int],
        dedicated_session_ids: List[int],
        ephemeral_session_ids: List[int],
        session_pool: str,
        session_parallel_requests: int,
        session_retry_rounds: int,
        timeout_seconds: Optional[int] = None,
        transport_timeout_buffer_seconds: Optional[int] = None,
        result_wait_attempts: Optional[int] = None,
        result_wait_interval_seconds: Optional[float] = None,
    ) -> Path:
        base = json.loads(self.base_config_path.read_text())
        cfg = copy.deepcopy(base)
        cortensor = cfg["providers"]["cortensor"]
        cortensor["api_key"] = str(self.cortensor_api_key)
        cortensor["api_key_env"] = "CORTENSOR_API_KEY"
        cortensor["api_mode"] = "cortensor_completion"
        cortensor["prompt_type"] = 1
        cortensor["session_id"] = int(session_id)
        cortensor["session_ids"] = _unique_positive_ints(session_ids)
        cortensor["session_pool"] = str(session_pool or "auto")
        cortensor["dedicated_session_ids"] = _unique_positive_ints(
            dedicated_session_ids
        )
        cortensor["ephemeral_session_ids"] = _unique_positive_ints(
            ephemeral_session_ids
        )
        cortensor["session_parallel_requests"] = max(1, int(session_parallel_requests))
        cortensor["session_retry_rounds"] = max(1, int(session_retry_rounds))
        cortensor["timeout_seconds"] = int(
            timeout_seconds
            if timeout_seconds is not None
            else self.args.timeout_seconds
        )
        cortensor["transport_timeout_buffer_seconds"] = int(
            transport_timeout_buffer_seconds
            if transport_timeout_buffer_seconds is not None
            else self.args.transport_timeout_buffer_seconds
        )
        cortensor["result_wait_attempts"] = int(
            result_wait_attempts
            if result_wait_attempts is not None
            else self.args.result_wait_attempts
        )
        cortensor["result_wait_interval_seconds"] = float(
            result_wait_interval_seconds
            if result_wait_interval_seconds is not None
            else self.args.result_wait_interval_seconds
        )
        cfg["gateway"]["api_turn_timeout_seconds"] = int(
            self.args.api_turn_timeout_seconds
        )
        tmp_dir = self.root / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        output_path = tmp_dir / (
            "cortensor-e2e-runtime-"
            + name_suffix
            + "-"
            + datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            + ".json"
        )
        output_path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
        return output_path

    def _run_case_with_retries(self, case_id: str, fn) -> None:
        attempts: List[Dict[str, Any]] = []
        eventual_ok = False
        for attempt in range(1, int(self.args.case_retries) + 1):
            try:
                ok, detail = fn()
            except Exception as exc:  # pragma: no cover - defensive
                ok = False
                detail = {"error": str(exc)}
            attempt_payload = {"attempt": attempt, "ok": ok, "detail": detail}
            attempts.append(attempt_payload)
            print(
                "CASE",
                case_id,
                json.dumps(
                    {
                        "attempt": attempt,
                        "ok": ok,
                        "summary": detail.get("summary")
                        if isinstance(detail, dict)
                        else None,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if ok:
                eventual_ok = True
                break
        self.results.append(
            {
                "case": case_id,
                "eventual_ok": eventual_ok,
                "attempts": attempts,
            }
        )

    def _run_cli_json(
        self,
        *,
        config_path: Path,
        command_args: List[str],
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        env = dict(self.env)
        if extra_env:
            env.update({str(k): str(v) for k, v in extra_env.items()})
        return _run_subprocess(
            [
                self.python_bin,
                "-m",
                "openminion.cli.main",
                "--config",
                str(config_path),
                *command_args,
            ],
            cwd=self.root,
            env=env,
            timeout_seconds=int(self.args.command_timeout_seconds),
        )

    def _run_agent_turn_json(
        self,
        *,
        message: str,
        session_id: str,
        config_path: Optional[Path] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        return self._run_cli_json(
            config_path=config_path or self.runtime_config,
            command_args=[
                "agent",
                "--message",
                message,
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--session-id",
                session_id,
                "--json",
            ],
            extra_env=extra_env,
        )

    def _run_gateway_once_json(
        self,
        *,
        message: str,
        session_id: str,
        config_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        return self._run_cli_json(
            config_path=config_path or self.runtime_config,
            command_args=[
                "gateway",
                "run",
                "--once",
                "--message",
                message,
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--session-id",
                session_id,
                "--json",
            ],
        )

    def case_agent_check_simple(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent-check",
                "--message",
                "Reply with exactly: cortensor-ok",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        ok = bool(result["ok"] and payload.get("ok") is True)
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_timeout_config_consistency(self) -> Tuple[bool, Dict[str, Any]]:
        runtime_cfg = json.loads(self.runtime_config.read_text())
        failover_cfg = json.loads(self.failover_config.read_text())
        runtime_cortensor = runtime_cfg.get("providers", {}).get("cortensor", {})
        failover_cortensor = failover_cfg.get("providers", {}).get("cortensor", {})
        runtime_gateway = runtime_cfg.get("gateway", {})

        checks = {
            "runtime_timeout_seconds": int(runtime_cortensor.get("timeout_seconds", -1))
            == int(self.args.timeout_seconds),
            "runtime_transport_timeout_buffer_seconds": int(
                runtime_cortensor.get("transport_timeout_buffer_seconds", -1)
            )
            == int(self.args.transport_timeout_buffer_seconds),
            "runtime_result_wait_attempts": int(
                runtime_cortensor.get("result_wait_attempts", -1)
            )
            == int(self.args.result_wait_attempts),
            "runtime_result_wait_interval_seconds": float(
                runtime_cortensor.get("result_wait_interval_seconds", -1.0)
            )
            == float(self.args.result_wait_interval_seconds),
            "runtime_api_turn_timeout_seconds": int(
                runtime_gateway.get("api_turn_timeout_seconds", -1)
            )
            == int(self.args.api_turn_timeout_seconds),
            "failover_timeout_seconds": int(
                failover_cortensor.get("timeout_seconds", -1)
            )
            == min(20, int(self.args.timeout_seconds)),
            "failover_transport_timeout_buffer_seconds": int(
                failover_cortensor.get("transport_timeout_buffer_seconds", -1)
            )
            == min(2, int(self.args.transport_timeout_buffer_seconds)),
            "failover_result_wait_attempts": int(
                failover_cortensor.get("result_wait_attempts", -1)
            )
            == 1,
            "failover_result_wait_interval_seconds": float(
                failover_cortensor.get("result_wait_interval_seconds", -1.0)
            )
            == 0.0,
        }

        return bool(all(checks.values())), {
            "summary": {
                "checks_passed": sum(1 for value in checks.values() if value),
                "checks_total": len(checks),
            },
            "checks": checks,
            "runtime_config": str(self.runtime_config),
            "failover_config": str(self.failover_config),
        }

    def case_agent_complex_structured_json(self) -> Tuple[bool, Dict[str, Any]]:
        session_id = "suite-complex-json-" + str(int(time.time()))
        prompt = (
            "Conformance test. Use these values: alpha=3, beta=5, gamma=8.\n"
            "Compute total=alpha+beta+gamma and weighted=alpha*1+beta*2+gamma*3.\n"
            "Return ONLY JSON (no markdown) with keys:\n"
            '{"status":"complex-ok","total":16,"weighted":37,"notes":["alpha","beta","gamma"]}'
        )
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent",
                "--message",
                prompt,
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--session-id",
                session_id,
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        text = str(payload.get("text", "")).strip()
        parsed_output = _extract_first_json_object(text)
        duration_ms = int(result.get("duration_ms") or 0)
        duration_budget_ms = int(self.args.command_timeout_seconds) * 1000
        ok = bool(
            result["ok"]
            and isinstance(parsed_output, dict)
            and parsed_output.get("status") == "complex-ok"
            and int(parsed_output.get("total", -1)) == 16
            and int(parsed_output.get("weighted", -1)) == 37
            and duration_ms < duration_budget_ms
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": duration_ms,
                "duration_budget_ms": duration_budget_ms,
                "parsed": bool(parsed_output),
            },
            "payload": payload,
            "parsed_output": parsed_output,
            "error": result.get("error"),
        }

    def case_agent_check_complex_input(self) -> Tuple[bool, Dict[str, Any]]:
        complex_input = (
            "Complex prompt-pack stress test.\n"
            "Data rows:\n"
            "- team=alpha region=us-west score=11 cost=5\n"
            "- team=beta region=us-east score=7 cost=3\n"
            "- team=gamma region=eu score=13 cost=8\n"
            "- team=delta region=apac score=4 cost=2\n"
            "Rules:\n"
            "1) Summaries must consider all rows.\n"
            "2) Prefer score/cost efficiency as tie-breaker.\n"
            "3) Mention any outlier row.\n"
            "Final output instruction (strict): Reply with exactly complex-input-ok"
        )
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent-check",
                "--message",
                complex_input,
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        metadata = (
            payload.get("metadata", {})
            if isinstance(payload.get("metadata"), dict)
            else {}
        )
        prompt_tokens = _safe_int(metadata.get("usage_prompt_tokens"), default=0)
        completion_tokens = _safe_int(
            metadata.get("usage_completion_tokens"), default=0
        )
        input_chars = len(complex_input)
        duration_ms = int(result.get("duration_ms") or 0)
        duration_budget_ms = int(self.args.command_timeout_seconds) * 1000
        ok = bool(
            result["ok"]
            and payload.get("ok") is True
            and str(metadata.get("provider", "")).strip().lower() == "cortensor"
            and input_chars >= 300
            and completion_tokens >= 1
            and duration_ms < duration_budget_ms
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": duration_ms,
                "duration_budget_ms": duration_budget_ms,
                "input_chars": input_chars,
                "usage_prompt_tokens": prompt_tokens,
                "usage_completion_tokens": completion_tokens,
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_agent_long_history_recall_json(self) -> Tuple[bool, Dict[str, Any]]:
        session_id = "suite-history-agent-" + str(int(time.time()))
        sentinel_a = "ALPHA-HIST-71"
        sentinel_b = "BETA-HIST-22"
        sentinel_c = "OMEGA-HIST-94"
        turns = [
            (
                "Turn 1 context seed. Facts:\n"
                "owner=Rina\ndeadline=2026-04-17\n"
                f"sentinel={sentinel_a}\n"
                + _build_long_block("turn1", repeat=8)
                + "\nReply briefly with: ack-turn-1"
            ),
            (
                "Turn 2 additional facts. Keep prior facts unchanged.\n"
                "project=Atlas-9\nincident=I-4481\n"
                f"sentinel={sentinel_b}\n"
                + _build_long_block("turn2", repeat=8)
                + "\nReply briefly with: ack-turn-2"
            ),
            (
                "Turn 3 policy note. Continue preserving all previous facts.\n"
                "region=us-west-2\npriority=high\n"
                f"sentinel={sentinel_c}\n"
                + _build_long_block("turn3", repeat=8)
                + "\nReply briefly with: ack-turn-3"
            ),
            (
                "Final validation. Using only session history from prior turns, return ONLY JSON:\n"
                "{"
                '"status":"history-session-ok",'
                '"owner":"Rina",'
                '"deadline":"2026-04-17",'
                '"sentinels":["ALPHA-HIST-71","BETA-HIST-22","OMEGA-HIST-94"]'
                "}"
            ),
        ]

        attempts: List[Dict[str, Any]] = []
        for message in turns:
            result = self._run_agent_turn_json(message=message, session_id=session_id)
            attempts.append(result)
            if not result.get("ok"):
                return False, {
                    "summary": {
                        "steps_completed": len(attempts),
                        "steps_total": len(turns),
                        "failed_step_exit_code": result.get("exit_code"),
                    },
                    "session_id": session_id,
                    "attempts": attempts,
                    "error": result.get("error"),
                }

        final_payload = attempts[-1].get("payload") or {}
        final_text = str(final_payload.get("text", "")).strip()
        parsed = _extract_first_json_object(final_text)
        json_ok = bool(
            isinstance(parsed, dict)
            and parsed.get("status") == "history-session-ok"
            and parsed.get("owner") == "Rina"
            and parsed.get("deadline") == "2026-04-17"
            and parsed.get("sentinels") == [sentinel_a, sentinel_b, sentinel_c]
        )
        text_ok = all(
            token in final_text
            for token in (
                "history-session-ok",
                "Rina",
                "2026-04-17",
                sentinel_a,
                sentinel_b,
                sentinel_c,
            )
        )
        total_duration_ms = sum(int(item.get("duration_ms") or 0) for item in attempts)
        max_duration_ms = int(self.args.command_timeout_seconds) * 1000 * len(turns)
        ok = bool((json_ok or text_ok) and total_duration_ms < max_duration_ms)
        return ok, {
            "summary": {
                "steps_total": len(turns),
                "total_duration_ms": total_duration_ms,
                "max_duration_ms": max_duration_ms,
                "json_ok": json_ok,
                "text_ok": text_ok,
            },
            "session_id": session_id,
            "final_payload": final_payload,
            "parsed_output": parsed,
        }

    def case_gateway_long_history_recall_json(self) -> Tuple[bool, Dict[str, Any]]:
        session_id = "suite-history-gateway-" + str(int(time.time()))
        first_key = "GW-FIRST-903"
        second_key = "GW-SECOND-117"
        turns = [
            (
                "Gateway history turn 1. Persist this key for later recall:\n"
                f"first_key={first_key}\n"
                + _build_long_block("gw-turn1", repeat=8)
                + "\nReply briefly with: gw-ack-1"
            ),
            (
                "Gateway history turn 2. Persist this second key while keeping the first key.\n"
                f"second_key={second_key}\n"
                + _build_long_block("gw-turn2", repeat=8)
                + "\nReply briefly with: gw-ack-2"
            ),
            (
                "Gateway final validation. Using this session's history only, return ONLY JSON:\n"
                "{"
                '"status":"gateway-history-ok",'
                '"keys":["GW-FIRST-903","GW-SECOND-117"]'
                "}"
            ),
        ]

        attempts: List[Dict[str, Any]] = []
        for message in turns:
            result = self._run_gateway_once_json(message=message, session_id=session_id)
            attempts.append(result)
            if not result.get("ok"):
                return False, {
                    "summary": {
                        "steps_completed": len(attempts),
                        "steps_total": len(turns),
                        "failed_step_exit_code": result.get("exit_code"),
                    },
                    "session_id": session_id,
                    "attempts": attempts,
                    "error": result.get("error"),
                }

        final_payload = attempts[-1].get("payload") or {}
        final_body = str(final_payload.get("body", "")).strip()
        parsed = _extract_first_json_object(final_body)
        json_ok = bool(
            isinstance(parsed, dict)
            and parsed.get("status") == "gateway-history-ok"
            and parsed.get("keys") == [first_key, second_key]
        )
        text_ok = all(
            token in final_body
            for token in ("gateway-history-ok", first_key, second_key)
        )
        total_duration_ms = sum(int(item.get("duration_ms") or 0) for item in attempts)
        max_duration_ms = int(self.args.command_timeout_seconds) * 1000 * len(turns)
        ok = bool((json_ok or text_ok) and total_duration_ms < max_duration_ms)
        return ok, {
            "summary": {
                "steps_total": len(turns),
                "total_duration_ms": total_duration_ms,
                "max_duration_ms": max_duration_ms,
                "json_ok": json_ok,
                "text_ok": text_ok,
            },
            "session_id": session_id,
            "final_payload": final_payload,
            "parsed_output": parsed,
        }

    def case_agent_check_weather(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent-check",
                "--message",
                "Run weather test using tool calls for San Francisco and Tokyo, then verify results.",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        metadata = (
            payload.get("metadata", {})
            if isinstance(payload.get("metadata"), dict)
            else {}
        )
        ok = bool(
            result["ok"]
            and payload.get("ok") is True
            and str(metadata.get("tool_verified", "")).lower() == "true"
            and str(metadata.get("tool_calls_count", "")) == "2"
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_agent_check_core_tools(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent-check",
                "--message",
                (
                    "Run core tools test with utc_now, calculate_expression, text_stats, "
                    "and weather test for San Francisco and Tokyo. Verify results."
                ),
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        metadata = (
            payload.get("metadata", {})
            if isinstance(payload.get("metadata"), dict)
            else {}
        )
        tool_results = _parse_tool_results(metadata)
        required_tools = {
            "utc_now",
            "calculate_expression",
            "text_stats",
            "lookup_weather",
        }
        tool_names = {
            str(item.get("tool_name", "")).strip()
            for item in tool_results
            if isinstance(item, dict)
        }
        required_present = required_tools.issubset(tool_names)
        required_verified = _required_tools_verified(tool_results, required_tools)
        ok = bool(
            result["ok"]
            and payload.get("ok") is True
            and str(metadata.get("tool_verified", "")).lower() == "true"
            and required_present
            and required_verified
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
                "tool_count": len(tool_results),
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_agent_session_continuity(self) -> Tuple[bool, Dict[str, Any]]:
        session_id = "suite-session-" + str(int(time.time()))
        first = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent",
                "--message",
                "Remember token BLUE42",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--session-id",
                session_id,
                "--json",
            ],
        )
        second = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "agent",
                "--message",
                "What token should you remember?",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--session-id",
                session_id,
                "--json",
            ],
        )
        storage_path = json.loads(self.runtime_config.read_text())["storage"]["path"]
        count_messages: Optional[int] = None
        try:
            conn = sqlite3.connect(storage_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            )
            row = cursor.fetchone()
            count_messages = int(row[0]) if row else 0
            conn.close()
        except Exception:
            count_messages = None

        ok = bool(
            first["ok"]
            and second["ok"]
            and isinstance(count_messages, int)
            and count_messages >= 4
        )
        return ok, {
            "summary": {
                "first_exit_code": first["exit_code"],
                "second_exit_code": second["exit_code"],
                "message_count": count_messages,
            },
            "session_id": session_id,
            "first_payload": first.get("payload"),
            "second_payload": second.get("payload"),
        }

    def case_agent_tool_chain_long(self) -> Tuple[bool, Dict[str, Any]]:
        session_id = "suite-toolchain-" + str(int(time.time()))
        debug_env = {
            "OPENMINION_LLM_DEBUG": "1",
            "OPENMINION_LLM_DEBUG_PROVIDER": "cortensor",
        }
        turns = [
            (
                "List files in the current working directory using the file_list_dir tool. "
                "Return the first 5 names and say TOOLCHAIN-1."
            ),
            (
                "Read README.md using the file_read tool. If it is missing, read openminion/README.md. "
                "Return the first 3 lines and say TOOLCHAIN-2."
            ),
            (
                "Compute (19*7)+11 using the calculate_expression tool. "
                "Return only the number and say TOOLCHAIN-3."
            ),
            (
                "Count words in: 'OpenMinion keeps tools safe' using the text_stats tool. "
                "Return the word count and say TOOLCHAIN-4."
            ),
            (
                "Get weather for San Francisco using lookup_weather tool. "
                "Return temperature and say TOOLCHAIN-5."
            ),
            (
                "Summarize results from TOOLCHAIN-1 to TOOLCHAIN-5. "
                "Include: one filename, first line of README, computed number, word count, and weather location. "
                "Say TOOLCHAIN-6."
            ),
        ]

        attempts: List[Dict[str, Any]] = []
        tool_turns_ok = 0
        for idx, message in enumerate(turns, start=1):
            result = self._run_agent_turn_json(
                message=message,
                session_id=session_id,
                extra_env=debug_env,
            )
            attempts.append(result)
            payload = result.get("payload") or {}
            metadata = (
                payload.get("metadata", {})
                if isinstance(payload.get("metadata"), dict)
                else {}
            )
            tool_calls = _safe_int(metadata.get("tool_calls_count"), 0)
            tool_verified = str(metadata.get("tool_verified", "")).lower() == "true"
            if idx <= 5 and tool_calls > 0 and tool_verified:
                tool_turns_ok += 1
            if not result.get("ok"):
                break

        ok = bool(tool_turns_ok >= 4 and len(attempts) == len(turns))
        return ok, {
            "summary": {
                "session_id": session_id,
                "steps_total": len(turns),
                "steps_completed": len(attempts),
                "tool_turns_ok": tool_turns_ok,
            },
            "attempts": attempts,
        }

    def case_gateway_once_simple(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "gateway",
                "run",
                "--once",
                "--message",
                "Reply with gateway-ok",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        ok = bool(
            result["ok"]
            and isinstance(payload.get("id"), str)
            and len(payload.get("id", "")) > 0
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_gateway_once_weather(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "gateway",
                "run",
                "--once",
                "--message",
                "Run weather test using tool calls for San Francisco and Tokyo, then verify results.",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        metadata = (
            payload.get("metadata", {})
            if isinstance(payload.get("metadata"), dict)
            else {}
        )
        ok = bool(
            result["ok"]
            and str(metadata.get("tool_verified", "")).lower() == "true"
            and str(metadata.get("tool_calls_count", "")) == "2"
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_gateway_once_core_tools(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "gateway",
                "run",
                "--once",
                "--message",
                (
                    "Run core tools test with utc_now, calculate_expression, text_stats, "
                    "and weather test for San Francisco and Tokyo. Verify results."
                ),
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        metadata = (
            payload.get("metadata", {})
            if isinstance(payload.get("metadata"), dict)
            else {}
        )
        tool_results = _parse_tool_results(metadata)
        required_tools = {
            "utc_now",
            "calculate_expression",
            "text_stats",
            "lookup_weather",
        }
        required_present = required_tools.issubset(
            {
                str(item.get("tool_name", "")).strip()
                for item in tool_results
                if isinstance(item, dict)
            }
        )
        required_verified = _required_tools_verified(tool_results, required_tools)
        ok = bool(
            result["ok"]
            and str(metadata.get("tool_verified", "")).lower() == "true"
            and required_present
            and required_verified
        )
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
                "tool_count": len(tool_results),
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_gateway_idempotency(self) -> Tuple[bool, Dict[str, Any]]:
        idempotency_key = "suite-idempotency-" + str(int(time.time()))
        first = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "gateway",
                "run",
                "--once",
                "--message",
                "Reply with idempotency-ok",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--idempotency-key",
                idempotency_key,
                "--json",
            ],
        )
        second = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "gateway",
                "run",
                "--once",
                "--message",
                "Reply with idempotency-ok",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--idempotency-key",
                idempotency_key,
                "--json",
            ],
        )
        first_id = (first.get("payload") or {}).get("id")
        second_id = (second.get("payload") or {}).get("id")
        ok = bool(first["ok"] and second["ok"] and first_id and first_id == second_id)
        return ok, {
            "summary": {"first_id": first_id, "second_id": second_id},
            "first_payload": first.get("payload"),
            "second_payload": second.get("payload"),
        }

    def case_doctor_check_turn(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "doctor",
                "--check-turn",
                "--message",
                "Doctor Cortensor health ping",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        summary = (
            payload.get("summary", {})
            if isinstance(payload.get("summary"), dict)
            else {}
        )
        ok = bool(result["ok"] and summary.get("ok") is True)
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_verify_smoke(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.runtime_config,
            command_args=[
                "verify",
                "smoke",
                "--message",
                "verify cortensor smoke",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        ok = bool(result["ok"] and payload.get("ok") is True)
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_bad_primary_failover(self) -> Tuple[bool, Dict[str, Any]]:
        result = self._run_cli_json(
            config_path=self.failover_config,
            command_args=[
                "agent-check",
                "--message",
                "Reply with exactly: failover-path-ok",
                "--target",
                self.args.target,
                "--channel",
                "console",
                "--json",
            ],
        )
        payload = result.get("payload") or {}
        ok = bool(result["ok"] and payload.get("ok") is True)
        return ok, {
            "summary": {
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
            },
            "payload": payload,
            "error": result.get("error"),
        }

    def case_api_bundle(self) -> Tuple[bool, Dict[str, Any]]:
        api_port = int(self.args.api_port)
        process = subprocess.Popen(
            [
                self.python_bin,
                "-m",
                "openminion.cli.main",
                "--config",
                str(self.runtime_config),
                "api",
                "run",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=str(self.root),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        summary = {
            "health": False,
            "turn_simple": False,
            "turn_weather": False,
            "session_messages": False,
        }
        details: Dict[str, Any] = {"errors": []}
        try:
            ready = False
            for _ in range(80):
                ok, code, payload, error = _http_json(
                    "GET",
                    f"http://127.0.0.1:{api_port}/health",
                    timeout_seconds=5,
                )
                if ok and code in (200, 503) and isinstance(payload, dict):
                    ready = True
                    break
                time.sleep(0.25)
            if not ready:
                details["errors"].append("api_not_ready")
                return False, {"summary": summary, "details": details}

            ok, code, payload, error = _http_json(
                "GET",
                f"http://127.0.0.1:{api_port}/health",
                timeout_seconds=20,
            )
            summary["health"] = bool(
                ok
                and code in (200, 503)
                and isinstance(payload, dict)
                and "ok" in payload
            )
            if not summary["health"]:
                details["errors"].append(f"health_failed:{error or code}")

            session_id = "api-suite-" + str(int(time.time()))
            ok, code, payload, error = _http_json(
                "POST",
                f"http://127.0.0.1:{api_port}/turns",
                body={
                    "message": "Reply with api-turn-ok",
                    "channel": "console",
                    "target": self.args.target,
                    "session_id": session_id,
                    "timeout_seconds": int(self.args.api_turn_timeout_seconds),
                },
                timeout_seconds=int(self.args.http_timeout_seconds),
            )
            summary["turn_simple"] = bool(
                ok
                and code == 200
                and isinstance(payload, dict)
                and payload.get("ok") is True
            )
            if not summary["turn_simple"]:
                details["errors"].append(f"turn_simple_failed:{error or code}")

            ok, code, payload, error = _http_json(
                "POST",
                f"http://127.0.0.1:{api_port}/turns",
                body={
                    "message": "Run weather test using tool calls for San Francisco and Tokyo, then verify results.",
                    "channel": "console",
                    "target": self.args.target,
                    "session_id": session_id,
                    "timeout_seconds": int(self.args.api_turn_timeout_seconds) + 20,
                },
                timeout_seconds=int(self.args.http_timeout_seconds) + 60,
            )
            turn_payload = (
                payload.get("turn", {})
                if isinstance(payload, dict) and isinstance(payload.get("turn"), dict)
                else {}
            )
            turn_metadata = (
                turn_payload.get("metadata", {})
                if isinstance(turn_payload.get("metadata"), dict)
                else {}
            )
            summary["turn_weather"] = bool(
                ok
                and code == 200
                and isinstance(payload, dict)
                and payload.get("ok") is True
                and str(turn_metadata.get("tool_verified", "")).lower() == "true"
            )
            if not summary["turn_weather"]:
                details["errors"].append(f"turn_weather_failed:{error or code}")

            ok, code, payload, error = _http_json(
                "GET",
                f"http://127.0.0.1:{api_port}/sessions/{quote(session_id, safe='')}/messages?limit=20",
                timeout_seconds=40,
            )
            messages = payload.get("messages", []) if isinstance(payload, dict) else []
            summary["session_messages"] = bool(
                ok and code == 200 and isinstance(messages, list) and len(messages) >= 2
            )
            if not summary["session_messages"]:
                details["errors"].append(f"session_messages_failed:{error or code}")
        finally:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

        return bool(all(summary.values())), {"summary": summary, "details": details}

    def run(self) -> Dict[str, Any]:
        self._run_case_with_retries(
            "timeout_config_consistency", self.case_timeout_config_consistency
        )
        self._run_case_with_retries("agent_check_simple", self.case_agent_check_simple)
        self._run_case_with_retries(
            "agent_check_complex_input", self.case_agent_check_complex_input
        )
        self._run_case_with_retries(
            "agent_complex_structured_json", self.case_agent_complex_structured_json
        )
        self._run_case_with_retries(
            "agent_check_weather", self.case_agent_check_weather
        )
        self._run_case_with_retries(
            "agent_check_core_tools", self.case_agent_check_core_tools
        )
        self._run_case_with_retries(
            "agent_session_continuity", self.case_agent_session_continuity
        )
        self._run_case_with_retries(
            "agent_tool_chain_long", self.case_agent_tool_chain_long
        )
        self._run_case_with_retries(
            "agent_long_history_recall_json", self.case_agent_long_history_recall_json
        )
        self._run_case_with_retries(
            "gateway_once_simple", self.case_gateway_once_simple
        )
        self._run_case_with_retries(
            "gateway_once_weather", self.case_gateway_once_weather
        )
        self._run_case_with_retries(
            "gateway_once_core_tools", self.case_gateway_once_core_tools
        )
        self._run_case_with_retries(
            "gateway_long_history_recall_json",
            self.case_gateway_long_history_recall_json,
        )
        self._run_case_with_retries(
            "gateway_idempotency_replay", self.case_gateway_idempotency
        )
        self._run_case_with_retries("doctor_check_turn", self.case_doctor_check_turn)
        self._run_case_with_retries("verify_smoke", self.case_verify_smoke)
        self._run_case_with_retries(
            "bad_primary_failover", self.case_bad_primary_failover
        )
        self._run_case_with_retries("api_bundle", self.case_api_bundle)

        passed = [item for item in self.results if item.get("eventual_ok")]
        failed = [item for item in self.results if not item.get("eventual_ok")]
        summary = {
            "generated_at_utc": _now_utc(),
            "total_cases": len(self.results),
            "passed_cases": len(passed),
            "failed_cases": len(failed),
            "pass_rate": round(len(passed) / len(self.results), 3)
            if self.results
            else 0.0,
            "failed_case_ids": [item.get("case") for item in failed],
            "openminion_home": str(self.openminion_home),
            "openminion_data_root": str(self.env.get("OPENMINION_DATA_ROOT", "")),
            "runtime_config": str(self.runtime_config),
            "failover_config": str(self.failover_config),
        }
        return {"summary": summary, "results": self.results}


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full live Cortensor E2E suite for OpenMinion."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="OpenMinion project root (default: current directory).",
    )
    parser.add_argument(
        "--base-config",
        default="test-configs/cortensor-e2e.json",
        help="Base config JSON path used to derive runtime configs.",
    )
    parser.add_argument(
        "--fallback-config",
        default="test-configs/per-agent.json",
        help=(
            "Fallback config used when --base-config is missing. "
            "Set empty value to disable fallback copy."
        ),
    )
    parser.add_argument(
        "--session-id", type=int, default=35, help="Primary Cortensor session id."
    )
    parser.add_argument(
        "--session-ids",
        default="35",
        help="Comma-separated additional session ids (failover pool).",
    )
    parser.add_argument(
        "--session-pool",
        default="auto",
        help="Session pool strategy: auto, dedicated, ephemeral, mixed.",
    )
    parser.add_argument(
        "--dedicated-session-ids",
        default="",
        help="Comma-separated dedicated session ids. Empty means no explicit dedicated pool.",
    )
    parser.add_argument(
        "--ephemeral-session-ids",
        default="",
        help="Comma-separated ephemeral session ids. Empty means no explicit ephemeral pool.",
    )
    parser.add_argument(
        "--session-parallel-requests",
        type=int,
        default=2,
        help="Number of sessions to race in parallel before failover.",
    )
    parser.add_argument(
        "--session-retry-rounds",
        type=int,
        default=2,
        help="How many full session-candidate passes to attempt before failing.",
    )
    parser.add_argument(
        "--bad-primary-session-id",
        type=int,
        default=999999,
        help="Invalid primary session id used by failover test case.",
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=40, help="Provider timeout_seconds."
    )
    parser.add_argument(
        "--transport-timeout-buffer-seconds",
        type=int,
        default=10,
        help="Provider transport timeout buffer.",
    )
    parser.add_argument(
        "--result-wait-attempts",
        type=int,
        default=3,
        help="Provider result_wait_attempts.",
    )
    parser.add_argument(
        "--result-wait-interval-seconds",
        type=float,
        default=1.0,
        help="Provider result_wait_interval_seconds.",
    )
    parser.add_argument(
        "--api-turn-timeout-seconds",
        type=int,
        default=140,
        help="Gateway/API turn timeout budget.",
    )
    parser.add_argument(
        "--api-port", type=int, default=19080, help="API port for bundle test."
    )
    parser.add_argument(
        "--command-timeout-seconds",
        type=int,
        default=320,
        help="Timeout budget per CLI command invocation.",
    )
    parser.add_argument(
        "--http-timeout-seconds",
        type=int,
        default=240,
        help="Timeout budget for API HTTP calls.",
    )
    parser.add_argument(
        "--case-retries",
        type=int,
        default=3,
        help="Retry attempts per case before marking failure.",
    )
    parser.add_argument(
        "--target", default="e2e-suite", help="Target identifier used for test turns."
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python executable used for CLI subprocess calls (default: current interpreter).",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional output JSON report path. Defaults to .tmp generated filename.",
    )
    return parser.parse_args(argv)


def _parse_tool_results(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []
    raw_results = metadata.get("tool_results", "")
    try:
        parsed = json.loads(str(raw_results or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _required_tools_verified(
    tool_results: List[Dict[str, Any]], required_tools: set[str]
) -> bool:
    verification_by_tool: Dict[str, bool] = {}
    for item in tool_results:
        tool_name = str(item.get("tool_name", "")).strip()
        if tool_name not in required_tools:
            continue
        is_ok = bool(item.get("ok"))
        is_verified = bool(item.get("verified"))
        verification_by_tool[tool_name] = verification_by_tool.get(
            tool_name, False
        ) or (is_ok and is_verified)
    return all(verification_by_tool.get(name, False) for name in required_tools)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    suite = CortensorE2ESuite(args)
    report = suite.run()
    default_report_path = (
        Path(args.root).resolve()
        / ".tmp"
        / (
            "cortensor-e2e-suite-"
            + datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            + ".json"
        )
    )
    report_path = (
        Path(args.report_path).resolve()
        if str(args.report_path).strip()
        else default_report_path
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print("REPORT_PATH", str(report_path), flush=True)
    print("SUMMARY", json.dumps(report.get("summary", {}), sort_keys=True), flush=True)
    return 0 if not report.get("summary", {}).get("failed_case_ids") else 1
