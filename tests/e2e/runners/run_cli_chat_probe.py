#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import sqlite3
import subprocess
import sys
import termios
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.modules.brain.paths import resolve_brain_sessions_db_path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[4]
OPENMINION_ROOT = FRAMEWORK_ROOT / "openminion"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_READY_PROMPT_RE = re.compile(r"(?:^|\n)\[[^\]\n]+\]\s+you>\s*$")
_CONFIRMATION_REQUIRED_RE = re.compile(r"Policy confirmation required\.", re.IGNORECASE)
_TIMEOUT_EXIT_CODE = 124
_PROBE_STATUS_PREFIX = "[probe-status]"
_AUTO_CONFIRM_LIMIT_ENV = "OPENMINION_LIVE_CLI_CHAT_AUTO_CONFIRM_LIMIT"
_AUTO_CONFIRM_LIMIT_DEFAULT = 32


def _open_probe_pty() -> tuple[int, int]:

    openpty = getattr(os, "openpty", None)
    if callable(openpty):
        return openpty()
    return pty.openpty()


def _configure_probe_slave_tty(slave_fd: int) -> None:

    attributes = termios.tcgetattr(slave_fd)
    local_modes = attributes[3]
    local_modes &= ~termios.ICANON
    attributes[3] = local_modes
    control_chars = attributes[6]
    control_chars[termios.VMIN] = 1
    control_chars[termios.VTIME] = 0
    termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _normalize_probe_text(text: str) -> str:
    return _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")


def _ready_prompt_detected(text: str) -> bool:
    return bool(_READY_PROMPT_RE.search(_normalize_probe_text(text)))


def _latest_prompt_requires_confirmation(previous: str, current: str) -> bool:
    normalized_current = _normalize_probe_text(current)
    normalized_previous = _normalize_probe_text(previous)
    delta = (
        normalized_current[len(normalized_previous) :]
        if normalized_current.startswith(normalized_previous)
        else normalized_current
    )
    if not delta:
        return False
    return bool(
        _CONFIRMATION_REQUIRED_RE.search(delta) and _ready_prompt_detected(delta)
    )


def _full_transcript(transcript: list[str], trailing_text: str = "") -> str:
    output = "".join(transcript)
    if trailing_text and not output.endswith(trailing_text):
        output += trailing_text
    return output


def _append_probe_status(
    output: str,
    *,
    phase: str,
    exit_code: int,
) -> str:
    suffix = f"\n{_PROBE_STATUS_PREFIX} phase={phase} exit_code={exit_code}\n"
    if output.endswith("\n"):
        return f"{output.rstrip()}{suffix}"
    return f"{output}{suffix}"


def _parse_probe_status(output: str) -> dict[str, Any] | None:
    marker = re.findall(
        rf"^{re.escape(_PROBE_STATUS_PREFIX)} phase=([^\s]+) exit_code=(-?\d+)\s*$",
        _normalize_probe_text(output),
        flags=re.MULTILINE,
    )
    if not marker:
        return None
    phase, exit_code = marker[-1]
    return {"phase": phase, "exit_code": int(exit_code)}


def _auto_confirm_limit() -> int:
    raw = str(os.getenv(_AUTO_CONFIRM_LIMIT_ENV, "")).strip()
    if not raw:
        return _AUTO_CONFIRM_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _AUTO_CONFIRM_LIMIT_DEFAULT
    return max(value, 1)


@dataclass(frozen=True, slots=True)
class _ProbeReadTimeout(TimeoutError):
    phase: str
    trailing_text: str = ""


@dataclass(frozen=True, slots=True)
class _ProbeWriteTimeout(TimeoutError):
    phase: str


def _brain_db_path(*, data_root: Path) -> Path:
    return resolve_brain_sessions_db_path(
        storage_path=data_root / "state" / "openminion.db"
    )


def _config_has_unset_runtime_env(
    config_path: Path,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[str, ...]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        return ()
    env_map = runtime.get("env")
    if not isinstance(env_map, dict):
        return ()
    active_env = environ if environ is not None else os.environ
    missing: list[str] = []
    for key, value in env_map.items():
        if str(value).strip() != "__SET_ME__":
            continue
        if str(active_env.get(str(key), "")).strip():
            continue
        missing.append(str(key))
    return tuple(sorted(missing))


def _resolve_home_root() -> Path:
    env_home = str(os.environ.get("OPENMINION_HOME", "")).strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    return FRAMEWORK_ROOT.resolve()


def _resolve_data_root(home_root: Path) -> Path:
    return (home_root / ".openminion").resolve()


def _resolve_cli_chat_e2e_root(home_root: Path) -> Path:
    return (_resolve_data_root(home_root) / "runtime" / "cli-chat-e2e").resolve()


def _default_probe_data_root(*, home_root: Path, session_id: str) -> Path:
    return (_resolve_cli_chat_e2e_root(home_root) / "data-roots" / session_id).resolve()


def _normalize_artifact_relative_path(raw_path: Path) -> Path:
    parts = raw_path.parts
    if parts[:2] == ("artifacts", "cli-chat-e2e"):
        return Path(*parts[2:]) if len(parts) > 2 else Path()
    if parts[:3] == (".openminion", "runtime", "cli-chat-e2e"):
        return Path(*parts[3:]) if len(parts) > 3 else Path()
    return raw_path


def _normalize_probe_path(
    *,
    raw_path: Path | None,
    home_root: Path,
    cwd: Path,
) -> Path | None:
    if raw_path is None:
        return None

    artifacts_root = _resolve_cli_chat_e2e_root(home_root)
    legacy_roots = [
        (FRAMEWORK_ROOT / "artifacts" / "cli-chat-e2e").resolve(),
        (OPENMINION_ROOT / "artifacts" / "cli-chat-e2e").resolve(),
    ]

    if raw_path.is_absolute():
        resolved = raw_path.resolve()
        for legacy_root in legacy_roots:
            try:
                relative = resolved.relative_to(legacy_root)
            except ValueError:
                continue
            return (artifacts_root / relative).resolve()
        return resolved

    normalized = _normalize_artifact_relative_path(raw_path)
    if normalized != raw_path:
        return (artifacts_root / normalized).resolve()
    return (cwd / raw_path).resolve()


def _find_conversation_session_id(
    *,
    data_root: Path,
    base_session_id: str,
) -> str | None:
    db_path = _brain_db_path(data_root=data_root)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            select session_id
            from sessions
            where session_id like ?
            order by updated_at desc
            limit 1
            """,
            (f"{base_session_id}::conv:%",),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def _query_session_events(
    *,
    data_root: Path,
    conversation_session_id: str,
) -> list[dict[str, Any]]:
    db_path = _brain_db_path(data_root=data_root)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            select event_type, payload_json
            from session_events
            where session_id = ?
            order by seq
            """,
            (conversation_session_id,),
        ).fetchall()
    finally:
        conn.close()

    events: list[dict[str, Any]] = []
    for event_type, payload_json in rows:
        try:
            raw_payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            raw_payload = {}
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        events.append({"type": str(event_type), "payload": payload})
    return events


def _collect_tool_audit_rows(
    *, data_root: Path
) -> tuple[list[str], list[dict[str, Any]]]:
    audit_paths = sorted(data_root.rglob("audit.jsonl"))
    rows: list[dict[str, Any]] = []
    for path in audit_paths:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                token = line.strip()
                if not token:
                    continue
                try:
                    payload = json.loads(token)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        except OSError:
            continue
    return [str(path) for path in audit_paths], rows


def _subset(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def _normalized_nonempty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coding_payload_hits(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        coding_keys = {
            key: value
            for key, value in payload.items()
            if str(key).startswith("coding.")
        }
        if coding_keys:
            hits.append({"type": event.get("type"), "payload": coding_keys})
    return hits


def _resume_markers(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        marker = _subset(
            payload,
            ("resume_count", "last_checkpoint_id", "task_backed_resume_state"),
        )
        if marker:
            markers.append({"type": event.get("type"), "payload": marker})
    return markers


def _inferred_dispatch_sites(
    *,
    bootstrap_events: list[dict[str, Any]],
    execution_status_events: list[dict[str, Any]],
) -> list[str]:
    resolved_profiles = {
        str(item.get("resolved_act_profile", "")).strip()
        for item in bootstrap_events
        if str(item.get("resolved_act_profile", "")).strip()
    }
    routes = {
        str(item.get("route", "")).strip()
        for item in execution_status_events
        if str(item.get("route", "")).strip()
    }
    dispatch_sites: list[str] = []
    if "act_loop_adaptive" in routes and "coding" in resolved_profiles:
        dispatch_sites.append(
            "adaptive.py: coding profile branch via act_loop_adaptive"
        )
    if "act_loop_adaptive" in routes and "general" in resolved_profiles:
        dispatch_sites.append("adaptive.py: general act path via act_loop_adaptive")
    if "act_profile_research" in routes and "research" in resolved_profiles:
        dispatch_sites.append(
            "execution/dispatch.py + bootstrap/resolve.py -> ResearchMode"
        )
    if "act:orchestrate" in routes and "orchestrate" in resolved_profiles:
        dispatch_sites.append("bootstrap/resolve.py -> OrchestrateMode")
    return dispatch_sites


def _build_summary(
    *,
    session_id: str,
    transcript_path: Path | None,
    events_path: Path | None,
    output: str,
    events: list[dict[str, Any]],
    audit_paths: list[str],
    audit_rows: list[dict[str, Any]],
    event_session_id: str | None,
) -> dict[str, Any]:
    event_types = [str(event.get("type", "")) for event in events]
    path_detected_payloads = [
        dict(event.get("payload", {}))
        for event in events
        if str(event.get("type", "")) == "brain.entry.path_detected"
        and isinstance(event.get("payload"), dict)
    ]
    bootstrap_payloads = [
        dict(event.get("payload", {}))
        for event in events
        if str(event.get("type", "")) == "brain.act.bootstrap"
        and isinstance(event.get("payload"), dict)
    ]
    entry_payloads = [
        dict(event.get("payload", {}))
        for event in events
        if str(event.get("type", "")) == "brain.entry"
        and isinstance(event.get("payload"), dict)
    ]
    execution_status_payloads = [
        dict(event.get("payload", {}))
        for event in events
        if str(event.get("type", "")) == "brain.execution_status"
        and isinstance(event.get("payload"), dict)
    ]
    observed_routes = sorted(
        {
            str(payload.get("route", "")).strip()
            for payload in execution_status_payloads
            if str(payload.get("route", "")).strip()
        }
    )
    observed_source_phases = sorted(
        {
            str(payload.get("source_phase", "")).strip()
            for payload in execution_status_payloads
            if str(payload.get("source_phase", "")).strip()
        }
    )
    observed_profiles = sorted(
        {
            text
            for payload in bootstrap_payloads
            if (text := _normalized_nonempty(payload.get("resolved_act_profile")))
        }
        | {
            text
            for payload in entry_payloads
            if (text := _normalized_nonempty(payload.get("act_profile")))
        }
        | {
            text
            for payload in (
                event.get("payload", {})
                for event in events
                if isinstance(event.get("payload"), dict)
            )
            if (text := _normalized_nonempty(payload.get("act_profile")))
        }
        | {
            text
            for row in audit_rows
            if (text := _normalized_nonempty(row.get("act_profile")))
        }
    )
    tool_names = sorted(
        {
            text
            for payload in (
                event.get("payload", {})
                for event in events
                if isinstance(event.get("payload"), dict)
            )
            if (text := _normalized_nonempty(payload.get("tool_name")))
        }
        | {
            text
            for row in audit_rows
            if (text := _normalized_nonempty(row.get("tool_name")))
        }
    )
    tool_failures = [
        row
        for row in audit_rows
        if str(row.get("event", "")).strip() in {"tool.failed", "tool.completed"}
        and (
            str(row.get("status", "")).strip().lower() == "failed"
            or bool(row.get("error"))
        )
    ]
    return {
        "session_id": session_id,
        "event_session_id": event_session_id,
        "transcript": str(transcript_path) if transcript_path is not None else None,
        "events": str(events_path) if events_path is not None else None,
        "tool_audit_paths": audit_paths,
        "probe_status": _parse_probe_status(output),
        "path_detected": path_detected_payloads,
        "brain_act_bootstrap": bootstrap_payloads,
        "brain_entry": entry_payloads,
        "execution_status": execution_status_payloads,
        "observed_routes": observed_routes,
        "observed_source_phases": observed_source_phases,
        "observed_act_profiles": observed_profiles,
        "tool_names": tool_names,
        "tool_event_count": len(audit_rows),
        "tool_failure_count": len(tool_failures),
        "coding_payload_hits": _coding_payload_hits(events),
        "resume_markers": _resume_markers(events),
        "event_type_counts": {
            event_type: event_types.count(event_type)
            for event_type in sorted(set(event_types))
        },
        "inferred_dispatch_sites": _inferred_dispatch_sites(
            bootstrap_events=bootstrap_payloads,
            execution_status_events=execution_status_payloads,
        ),
    }


def _read_until(
    *,
    master_fd: int,
    transcript: list[str],
    predicate,
    timeout_seconds: float,
    phase: str,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    combined = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([master_fd], [], [], min(0.05, remaining))
            if not ready:
                continue
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            continue
        except OSError:
            break
        if not chunk:
            continue
        text = chunk.decode("utf-8", errors="replace")
        transcript.append(text)
        combined += text
        if predicate(combined):
            return combined
    trailing_reads = 0
    while trailing_reads < 3:
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            break
        except OSError:
            break
        if not chunk:
            break
        text = chunk.decode("utf-8", errors="replace")
        transcript.append(text)
        combined += text
        if predicate(combined):
            return combined
        trailing_reads += 1
    raise _ProbeReadTimeout(phase=phase, trailing_text=combined)


def _drain_until_process_exit(
    *,
    master_fd: int,
    proc: subprocess.Popen[str],
    transcript: list[str],
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if proc.poll() is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            ready, _, _ = select.select([master_fd], [], [], min(0.05, remaining))
        except OSError:
            ready = []
        if not ready:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            continue
        except OSError:
            break
        if not chunk:
            continue
        transcript.append(chunk.decode("utf-8", errors="replace"))

    trailing_reads = 0
    while trailing_reads < 3:
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            break
        except OSError:
            break
        if not chunk:
            break
        transcript.append(chunk.decode("utf-8", errors="replace"))
        trailing_reads += 1
    return True


def _write_all(
    *,
    master_fd: int,
    payload: bytes,
    timeout_seconds: float,
    phase: str,
    transcript: list[str] | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _ProbeWriteTimeout(phase=phase)
        try:
            written = os.write(master_fd, view[offset:])
        except BlockingIOError:
            if transcript is not None:
                _drain_available(master_fd=master_fd, transcript=transcript)
            select.select([], [master_fd], [], min(0.05, remaining))
            continue
        except InterruptedError:
            continue
        if written <= 0:
            if transcript is not None:
                _drain_available(master_fd=master_fd, transcript=transcript)
            select.select([], [master_fd], [], min(0.05, remaining))
            continue
        offset += written


def _drain_available(*, master_fd: int, transcript: list[str]) -> None:
    while True:
        try:
            ready, _, _ = select.select([master_fd], [], [], 0)
        except OSError:
            return
        if not ready:
            return
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            return
        except OSError:
            return
        if not chunk:
            return
        transcript.append(chunk.decode("utf-8", errors="replace"))


def _shutdown_timeout_can_count_as_success(output: str) -> bool:
    normalized = _normalize_probe_text(output).rstrip()
    return bool(normalized) and normalized.endswith("you> /exit")


def _expand_probe_messages(messages: list[str]) -> list[str]:
    expanded: list[str] = []
    for message in messages:
        buffered_turn: list[str] = []
        for line in str(message or "").splitlines():
            token = line.strip()
            if not token:
                continue
            if token.startswith("/"):
                if buffered_turn:
                    expanded.append(" ".join(buffered_turn))
                    buffered_turn = []
                expanded.append(token)
                continue
            buffered_turn.append(token)
        if buffered_turn:
            expanded.append(" ".join(buffered_turn))
    return expanded


def _maybe_dump_debug_on_exit(
    *,
    master_fd: int,
    transcript: list[str],
    timeout_seconds: float,
) -> None:
    try:
        _write_all(
            master_fd=master_fd,
            payload=b"/debug\n",
            timeout_seconds=max(2.0, min(10.0, timeout_seconds / 4.0)),
            phase="debug_write_timeout",
            transcript=transcript,
        )
    except (OSError, _ProbeWriteTimeout):
        return
    debug_timeout = max(2.0, min(10.0, timeout_seconds / 4.0))
    try:
        _read_until(
            master_fd=master_fd,
            transcript=transcript,
            predicate=_ready_prompt_detected,
            timeout_seconds=debug_timeout,
            phase="debug_dump_timeout",
        )
    except _ProbeReadTimeout:
        # Preserve any trailing transcript already captured, but do not
        # reclassify the probe; the dump is observability-only.
        return


def _run_probe_session(
    *,
    cmd: list[str],
    env: dict[str, str],
    cwd: str,
    messages: list[str],
    timeout_seconds: float,
    dump_debug_on_exit: bool = False,
    auto_confirm: bool = False,
) -> tuple[int, str]:
    master_fd, slave_fd = _open_probe_pty()
    _configure_probe_slave_tty(slave_fd)
    proc = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=cwd,
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    transcript: list[str] = []
    messages_completed = False
    expanded_messages = _expand_probe_messages(messages)
    caller_requested_exit = bool(expanded_messages) and expanded_messages[-1] in {
        "/exit",
        "/quit",
    }

    try:
        try:
            _read_until(
                master_fd=master_fd,
                transcript=transcript,
                predicate=_ready_prompt_detected,
                timeout_seconds=timeout_seconds,
                phase="startup_timeout",
            )
        except _ProbeReadTimeout as exc:
            return _TIMEOUT_EXIT_CODE, _append_probe_status(
                _full_transcript(transcript, exc.trailing_text),
                phase=exc.phase,
                exit_code=_TIMEOUT_EXIT_CODE,
            )
        for index, message in enumerate(expanded_messages):
            is_terminal_exit_command = index == len(
                expanded_messages
            ) - 1 and message in {"/exit", "/quit"}
            previous_output = _full_transcript(transcript)
            try:
                _write_all(
                    master_fd=master_fd,
                    payload=(message + "\n").encode("utf-8"),
                    timeout_seconds=timeout_seconds,
                    phase="input_write_timeout",
                    transcript=transcript,
                )
            except _ProbeWriteTimeout as exc:
                return _TIMEOUT_EXIT_CODE, _append_probe_status(
                    _full_transcript(transcript),
                    phase=exc.phase,
                    exit_code=_TIMEOUT_EXIT_CODE,
                )
            if is_terminal_exit_command:
                break
            try:
                current_output = _read_until(
                    master_fd=master_fd,
                    transcript=transcript,
                    predicate=_ready_prompt_detected,
                    timeout_seconds=timeout_seconds,
                    phase="turn_timeout",
                )
            except _ProbeReadTimeout as exc:
                return _TIMEOUT_EXIT_CODE, _append_probe_status(
                    _full_transcript(transcript, exc.trailing_text),
                    phase=exc.phase,
                    exit_code=_TIMEOUT_EXIT_CODE,
                )
            if auto_confirm:
                confirmation_turns = 0
                confirmation_limit = _auto_confirm_limit()
                while _latest_prompt_requires_confirmation(
                    previous_output,
                    current_output,
                ):
                    confirmation_turns += 1
                    if confirmation_turns > confirmation_limit:
                        return _TIMEOUT_EXIT_CODE, _append_probe_status(
                            _full_transcript(transcript),
                            phase="confirmation_loop_limit",
                            exit_code=_TIMEOUT_EXIT_CODE,
                        )
                    previous_output = current_output
                    try:
                        _write_all(
                            master_fd=master_fd,
                            payload=b"yes\n",
                            timeout_seconds=timeout_seconds,
                            phase="confirmation_write_timeout",
                            transcript=transcript,
                        )
                    except _ProbeWriteTimeout as exc:
                        return _TIMEOUT_EXIT_CODE, _append_probe_status(
                            _full_transcript(transcript),
                            phase=exc.phase,
                            exit_code=_TIMEOUT_EXIT_CODE,
                        )
                    try:
                        current_output = _read_until(
                            master_fd=master_fd,
                            transcript=transcript,
                            predicate=_ready_prompt_detected,
                            timeout_seconds=timeout_seconds,
                            phase="confirmation_timeout",
                        )
                    except _ProbeReadTimeout as exc:
                        return _TIMEOUT_EXIT_CODE, _append_probe_status(
                            _full_transcript(transcript, exc.trailing_text),
                            phase=exc.phase,
                            exit_code=_TIMEOUT_EXIT_CODE,
                        )
        messages_completed = True
        if dump_debug_on_exit:
            _maybe_dump_debug_on_exit(
                master_fd=master_fd,
                transcript=transcript,
                timeout_seconds=timeout_seconds,
            )
        if caller_requested_exit:
            output = _full_transcript(transcript)
            if proc.poll() is None:
                exited = _drain_until_process_exit(
                    master_fd=master_fd,
                    proc=proc,
                    transcript=transcript,
                    timeout_seconds=max(5.0, min(30.0, timeout_seconds / 6.0)),
                )
                if not exited:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    output = _full_transcript(transcript)
                    if messages_completed and _shutdown_timeout_can_count_as_success(
                        output
                    ):
                        return 0, output
                    return _TIMEOUT_EXIT_CODE, _append_probe_status(
                        output,
                        phase="shutdown_timeout",
                        exit_code=_TIMEOUT_EXIT_CODE,
                    )
            output = _full_transcript(transcript)
            if proc.returncode not in (0, None):
                return int(proc.returncode), _append_probe_status(
                    output,
                    phase="child_nonzero_exit",
                    exit_code=int(proc.returncode),
                )
            return 0, output
        try:
            _write_all(
                master_fd=master_fd,
                payload=b"/exit\n",
                timeout_seconds=timeout_seconds,
                phase="shutdown_write_timeout",
                transcript=transcript,
            )
        except (OSError, _ProbeWriteTimeout):
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
            output = _full_transcript(transcript)
            if proc.returncode not in (0, None):
                return int(proc.returncode), _append_probe_status(
                    output,
                    phase="child_nonzero_exit",
                    exit_code=int(proc.returncode),
                )
            return _TIMEOUT_EXIT_CODE, _append_probe_status(
                output,
                phase="shutdown_timeout",
                exit_code=_TIMEOUT_EXIT_CODE,
            )
        shutdown_timeout = max(5.0, min(30.0, timeout_seconds / 6.0))
        exited = _drain_until_process_exit(
            master_fd=master_fd,
            proc=proc,
            transcript=transcript,
            timeout_seconds=shutdown_timeout,
        )
        if not exited:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            output = _full_transcript(transcript)
            if messages_completed and _shutdown_timeout_can_count_as_success(output):
                return 0, output
            return _TIMEOUT_EXIT_CODE, _append_probe_status(
                output,
                phase="shutdown_timeout",
                exit_code=_TIMEOUT_EXIT_CODE,
            )
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    output = _full_transcript(transcript)
    if proc.returncode not in (0, None):
        return int(proc.returncode), _append_probe_status(
            output,
            phase="child_nonzero_exit",
            exit_code=int(proc.returncode),
        )
    return 0, output


def _write_probe_artifacts(
    *,
    transcript_path: Path | None,
    events_path: Path | None,
    summary_path: Path | None,
    session_id: str,
    output: str,
    events: list[dict[str, Any]],
    audit_paths: list[str],
    audit_rows: list[dict[str, Any]],
    event_session_id: str | None,
) -> None:
    if transcript_path is not None:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(output, encoding="utf-8")
    if events_path is not None:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            json.dumps(events, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary = _build_summary(
            session_id=session_id,
            transcript_path=transcript_path,
            events_path=events_path,
            output=output,
            events=events,
            audit_paths=audit_paths,
            audit_rows=audit_rows,
            event_session_id=event_session_id,
        )
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one PTY-backed OpenMinion chat probe."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--message", action="append", required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--python", default=".venv/bin/python3.11")
    parser.add_argument("--output")
    parser.add_argument("--events-output")
    parser.add_argument("--summary-output")
    parser.add_argument("--data-root")
    parser.add_argument("--trace-root")
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument(
        "--dump-debug-on-exit",
        action="store_true",
        help="Send /debug and capture its JSON block before /exit.",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Keep interactive phase-status lines enabled during the probe.",
    )
    args = parser.parse_args()

    cmd = [
        args.python,
        "-m",
        "openminion",
        "--config",
        args.config,
        "chat",
        "--agent",
        args.agent,
        "--session",
        args.session,
        "--quiet",
    ]
    if not args.show_progress:
        cmd.append("--no-progress")

    cwd = Path(os.getcwd()).resolve()
    home_root = _resolve_home_root()
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "src")
    env.setdefault("OPENMINION_HOME", str(home_root))
    if args.data_root:
        normalized_data_root = _normalize_probe_path(
            raw_path=Path(args.data_root),
            home_root=home_root,
            cwd=cwd,
        )
        env["OPENMINION_DATA_ROOT"] = str(normalized_data_root)
    else:
        env.setdefault(
            "OPENMINION_DATA_ROOT",
            str(_default_probe_data_root(home_root=home_root, session_id=args.session)),
        )
    if args.trace_root:
        env["OPENMINION_TRACE_REQUESTS"] = "1"
        normalized_trace_root = _normalize_probe_path(
            raw_path=Path(args.trace_root),
            home_root=home_root,
            cwd=cwd,
        )
        env["OPENMINION_TRACE_REQUESTS_DIR"] = str(normalized_trace_root)
    if args.max_ticks is not None:
        env["OPENMINION_BRAIN_MAX_TICKS"] = str(max(1, int(args.max_ticks)))
    transcript_path = _normalize_probe_path(
        raw_path=Path(args.output) if args.output else None,
        home_root=home_root,
        cwd=cwd,
    )
    events_path = _normalize_probe_path(
        raw_path=Path(args.events_output) if args.events_output else None,
        home_root=home_root,
        cwd=cwd,
    )
    summary_path = _normalize_probe_path(
        raw_path=Path(args.summary_output) if args.summary_output else None,
        home_root=home_root,
        cwd=cwd,
    )
    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        output = _append_probe_status(
            f"missing config file: {config_path}\n",
            phase="config_missing",
            exit_code=2,
        )
        _write_probe_artifacts(
            transcript_path=transcript_path,
            events_path=events_path,
            summary_path=summary_path,
            session_id=args.session,
            output=output,
            events=[],
            audit_paths=[],
            audit_rows=[],
            event_session_id=None,
        )
        sys.stdout.write(output)
        return 2
    missing_env = _config_has_unset_runtime_env(config_path, environ=env)
    if missing_env:
        output = _append_probe_status(
            (
                "missing live provider env for config "
                f"{config_path}: {', '.join(missing_env)}\n"
            ),
            phase="config_env_missing",
            exit_code=2,
        )
        _write_probe_artifacts(
            transcript_path=transcript_path,
            events_path=events_path,
            summary_path=summary_path,
            session_id=args.session,
            output=output,
            events=[],
            audit_paths=[],
            audit_rows=[],
            event_session_id=None,
        )
        sys.stdout.write(output)
        return 2
    python_path = Path(args.python).expanduser()
    if not python_path.exists():
        output = _append_probe_status(
            f"missing python interpreter: {python_path}\n",
            phase="python_missing",
            exit_code=2,
        )
        _write_probe_artifacts(
            transcript_path=transcript_path,
            events_path=events_path,
            summary_path=summary_path,
            session_id=args.session,
            output=output,
            events=[],
            audit_paths=[],
            audit_rows=[],
            event_session_id=None,
        )
        sys.stdout.write(output)
        return 2
    exit_code, output = _run_probe_session(
        cmd=cmd,
        env=env,
        cwd=str(cwd),
        messages=list(args.message),
        timeout_seconds=args.timeout,
        dump_debug_on_exit=args.dump_debug_on_exit,
    )

    data_root = Path(env["OPENMINION_DATA_ROOT"]).resolve()
    event_session_id = _find_conversation_session_id(
        data_root=data_root,
        base_session_id=args.session,
    )
    events = (
        _query_session_events(
            data_root=data_root,
            conversation_session_id=event_session_id,
        )
        if event_session_id
        else []
    )
    audit_paths, audit_rows = _collect_tool_audit_rows(data_root=data_root)
    _write_probe_artifacts(
        transcript_path=transcript_path,
        events_path=events_path,
        summary_path=summary_path,
        session_id=args.session,
        output=output,
        events=events,
        audit_paths=audit_paths,
        audit_rows=audit_rows,
        event_session_id=event_session_id,
    )
    sys.stdout.write(output)
    return exit_code
