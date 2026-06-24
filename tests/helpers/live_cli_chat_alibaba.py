from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from tests.e2e.runners.run_cli_chat_probe import _run_probe_session
from tests.helpers.live_e2e_profiles import resolve_live_config_path

RAW_TOOL_MARKUP_RE = re.compile(
    r"<minimax:tool_call>|<tool_call>|<functioncall>|<invoke\s+name=|\[tool_call\]",
    re.IGNORECASE,
)
_PROVIDER_AUTH_REJECTION_MARKERS = (
    "rejected authentication for this turn",
    "invalid access token or token expired",
)
_PROVIDER_QUOTA_REJECTION_MARKERS = (
    "quota, billing, or rate-limit block",
    "usage limit exceeded",
    "requires more credits",
    "insufficient credits",
)
_COMPLETION_CONTRACT_FAILURE_MARKERS = (
    "required completion contract",
    "finalization_status contract",
    "model ended the turn without the required completion contract",
)
LIVE_CLI_CHAT_TIMEOUT_ENV = "OPENMINION_LIVE_CLI_CHAT_E2E_TIMEOUT"
LIVE_SKILL_SIMPLE_TIMEOUT_ENV = "OPENMINION_LIVE_SKILL_SIMPLE_E2E_TIMEOUT"
LIVE_SKILL_DENSE_TIMEOUT_ENV = "OPENMINION_LIVE_SKILL_DENSE_E2E_TIMEOUT"
LIVE_CODING_PROJECT_TIMEOUT_ENV = "OPENMINION_LIVE_CODING_PROJECT_E2E_TIMEOUT"
_TIMEOUT_DEFAULTS = {
    "generic": 180,
    "skill_simple": 180,
    "skill_dense": 420,
    "coding_project": 1200,
}
_MATRIX_TIMEOUT_ENVS = {
    "skill_simple": LIVE_SKILL_SIMPLE_TIMEOUT_ENV,
    "skill_dense": LIVE_SKILL_DENSE_TIMEOUT_ENV,
    "coding_project": LIVE_CODING_PROJECT_TIMEOUT_ENV,
}


@dataclass(frozen=True)
class CLISessionResult:
    session_id: str
    transcript: str
    transcript_path: Path
    data_root: Path
    trace_root: Path


def openminion_root() -> Path:
    return Path(__file__).resolve().parents[2]


def framework_root() -> Path:
    return openminion_root().parent


def default_config_path() -> Path:
    override = str(os.getenv("OPENMINION_LIVE_CLI_CHAT_CONFIG", "")).strip()
    if override:
        return resolve_live_config_path(override, framework_root())
    return framework_root() / "test-configs" / "per-agent-alibaba-minimax.json"


def default_agent_id() -> str:
    return str(os.getenv("OPENMINION_LIVE_CLI_CHAT_AGENT_ID", "")).strip() or (
        "alibaba-minimax"
    )


def python_bin() -> Path:
    configured = str(os.getenv("OPENMINION_PYTHON", "")).strip()
    if configured:
        return Path(configured)
    return openminion_root() / ".venv" / "bin" / "python3.11"


def _config_has_unset_runtime_env(config_path: Path) -> tuple[str, ...]:
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
    missing: list[str] = []
    for key, value in env_map.items():
        if str(value).strip() != "__SET_ME__":
            continue
        if str(os.getenv(str(key), "")).strip():
            continue
        missing.append(str(key))
    return tuple(sorted(missing))


def require_live_flag() -> None:
    if str(os.getenv("OPENMINION_LIVE_CLI_CHAT_E2E", "")).strip() != "1":
        pytest.skip(
            "OPENMINION_LIVE_CLI_CHAT_E2E=1 not set; skipping live CLI chat E2E."
        )


def timeout_seconds(matrix_type: str = "generic") -> int:
    normalized_matrix_type = (
        matrix_type if matrix_type in _TIMEOUT_DEFAULTS else "generic"
    )
    default_value = _TIMEOUT_DEFAULTS[normalized_matrix_type]
    matrix_override_env = _MATRIX_TIMEOUT_ENVS.get(normalized_matrix_type)
    raw = str(os.getenv(matrix_override_env, "")).strip() if matrix_override_env else ""
    if not raw:
        raw = str(os.getenv(LIVE_CLI_CHAT_TIMEOUT_ENV, str(default_value))).strip()
    if not raw:
        raw = str(default_value)
    try:
        value = int(raw)
    except ValueError:
        return default_value
    return max(value, 30)


def artifact_dir() -> Path:
    # Keep live E2E evidence under the framework-root generated-runtime tree
    # regardless of ambient OPENMINION_HOME / generated-root overrides.
    root = framework_root() / ".openminion" / "runtime" / "cli-chat-e2e"
    root.mkdir(parents=True, exist_ok=True)
    return root


def format_prompt(template: str) -> str:
    return template.format(
        framework_root=framework_root(),
        openminion_root=openminion_root(),
    )


def extract_all_debug_payloads(transcript: str) -> list[dict]:
    payloads = extract_debug_payloads(transcript, which="all")
    assert isinstance(payloads, list)
    return payloads


def extract_debug_payloads(
    transcript: str,
    *,
    which: Literal["all", "first", "last"] = "last",
) -> dict | list[dict]:
    decoder = json.JSONDecoder()
    payloads: list[dict] = []
    index = 0
    while index < len(transcript):
        start = transcript.find("{", index)
        if start < 0:
            break
        try:
            payload, end = decoder.raw_decode(transcript[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(payload, dict) and isinstance(payload.get("last_turn"), dict):
            payloads.append(payload)
            index = start + end
            continue
        index = start + 1
    if which == "all":
        return payloads
    if not payloads:
        raise AssertionError(
            "could not find /debug JSON payload in CLI transcript\n"
            f"transcript_tail={transcript[-2000:]}"
        )
    if which == "first":
        return payloads[0]
    if which == "last":
        return payloads[-1]
    raise ValueError(f"unknown debug payload selector: {which!r}")


def extract_last_debug_payload(transcript: str) -> dict:
    payload = extract_debug_payloads(transcript, which="last")
    assert isinstance(payload, dict)
    return payload


def parse_tool_results(raw_value: object) -> list[dict]:
    if isinstance(raw_value, list):
        return [item for item in raw_value if isinstance(item, dict)]
    if isinstance(raw_value, dict):
        return [raw_value]
    if isinstance(raw_value, str):
        token = raw_value.strip()
        if not token:
            return []
        try:
            parsed = json.loads(token)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def has_completion_contract_failure(last_turn: object) -> bool:
    if not isinstance(last_turn, dict):
        return False
    body_preview = str(last_turn.get("body_preview", "") or "").lower()
    failure_message = str(last_turn.get("failure_message", "") or "").lower()
    token = " ".join((body_preview, failure_message))
    return any(marker in token for marker in _COMPLETION_CONTRACT_FAILURE_MARKERS)


def _strip_agent_namespace(text: str, *, agent_id: str) -> str:
    cleaned = str(text or "").strip()
    prefix = f"{agent_id}:"
    if cleaned.startswith(prefix):
        return cleaned[len(prefix) :].strip()
    return cleaned


def _is_runtime_control_message(text: str) -> bool:
    return str(text or "").strip().startswith("Policy confirmation required.")


def _extract_assistant_messages_prose(
    *,
    transcript: str,
    session_id: str,
    agent_id: str,
    include_policy_confirmation_prompt: bool,
) -> list[str]:
    prefix = f"[{session_id}|{agent_id}] {agent_id}:"
    messages: list[str] = []
    start = 0
    while True:
        match = transcript.find(prefix, start)
        if match < 0:
            break
        body_start = match + len(prefix)
        boundary_candidates = [
            pos
            for pos in (
                transcript.find("\n[chat]", body_start),
                transcript.find(f"\n[{session_id}|{agent_id}] you>", body_start),
                transcript.find(prefix, body_start),
            )
            if pos >= 0
        ]
        body_end = min(boundary_candidates) if boundary_candidates else len(transcript)
        lines = transcript[body_start:body_end].splitlines()
        cleaned = "\n".join(line.strip() for line in lines if line.strip())
        include_runtime_control = (
            include_policy_confirmation_prompt and _is_runtime_control_message(cleaned)
        )
        if cleaned and (
            include_runtime_control or not _is_runtime_control_message(cleaned)
        ):
            messages.append(cleaned)
        start = max(body_end, body_start)
    return messages


def _extract_assistant_messages_structured(
    *,
    transcript: str,
    agent_id: str,
    include_policy_confirmation_prompt: bool,
) -> list[str]:
    messages: list[str] = []
    for payload in extract_all_debug_payloads(transcript):
        last_turn = payload.get("last_turn")
        if not isinstance(last_turn, dict):
            continue
        body = str(last_turn.get("body", "") or "").strip()
        if not body:
            body = str(last_turn.get("body_preview", "") or "").strip()
        body = _strip_agent_namespace(body, agent_id=agent_id)
        metadata = last_turn.get("metadata")
        respond_kind = (
            str(metadata.get("respond_kind", "") or "").strip()
            if isinstance(metadata, dict)
            else ""
        )
        include_runtime_control = (
            include_policy_confirmation_prompt
            and respond_kind == "policy_confirmation_prompt"
            and _is_runtime_control_message(body)
        )
        if body and (include_runtime_control or not _is_runtime_control_message(body)):
            messages.append(body)
    return messages


def extract_assistant_messages(
    *,
    transcript: str,
    session_id: str,
    agent_id: str,
    include_policy_confirmation_prompt: bool = True,
) -> list[str]:
    prose_messages = _extract_assistant_messages_prose(
        transcript=transcript,
        session_id=session_id,
        agent_id=agent_id,
        include_policy_confirmation_prompt=include_policy_confirmation_prompt,
    )
    if prose_messages:
        return prose_messages
    return _extract_assistant_messages_structured(
        transcript=transcript,
        agent_id=agent_id,
        include_policy_confirmation_prompt=include_policy_confirmation_prompt,
    )


def is_unknown_tool_flake(text: str) -> bool:
    return str(text).strip().lower().startswith("unknown tool:")


def skip_if_provider_auth_rejected(
    *, transcript: str, transcript_path: Path, context: str
) -> None:
    lowered = str(transcript or "").lower()
    if any(marker in lowered for marker in _PROVIDER_AUTH_REJECTION_MARKERS):
        pytest.skip(
            f"{context}: provider rejected authentication; transcript={transcript_path}"
        )


def skip_if_provider_quota_rejected(
    *, transcript: str, transcript_path: Path, context: str
) -> None:
    lowered = str(transcript or "").lower()
    if any(marker in lowered for marker in _PROVIDER_QUOTA_REJECTION_MARKERS):
        pytest.skip(
            f"{context}: provider quota/billing unavailable; transcript={transcript_path}"
        )


def skip_if_completion_contract_failed(
    *, last_turn: object, transcript_path: Path, context: str
) -> None:
    if has_completion_contract_failure(last_turn):
        pytest.skip(
            f"{context}: live target did not satisfy the required completion "
            f"contract; transcript={transcript_path}"
        )


def run_cli_session(
    *,
    session_id_prefix: str,
    user_input: str,
    agent_id: str | None = None,
    config_path: Path | None = None,
    attempt_suffix: str = "",
    data_root_override: Path | None = None,
    workspace_root_override: Path | None = None,
    matrix_type: str = "generic",
    auto_confirm: bool = False,
) -> CLISessionResult:
    require_live_flag()

    resolved_config = config_path or default_config_path()
    if not resolved_config.exists():
        pytest.skip(f"missing config file: {resolved_config}")
    missing_env = _config_has_unset_runtime_env(resolved_config)
    if missing_env:
        pytest.skip(
            f"missing live provider env for config {resolved_config}: {', '.join(missing_env)}"
        )

    resolved_python = python_bin()
    if not resolved_python.exists():
        pytest.skip(f"missing python interpreter: {resolved_python}")

    suffix = f"-{attempt_suffix}" if attempt_suffix else ""
    session_id = f"{session_id_prefix}{suffix}-{uuid.uuid4().hex[:8]}"
    artifacts_root = artifact_dir()
    transcript_path = artifacts_root / f"{session_id}.txt"
    data_root = (
        data_root_override
        if data_root_override is not None
        else artifacts_root / "data-roots" / session_id
    )
    trace_root = artifacts_root / "traces" / session_id
    data_root.mkdir(parents=True, exist_ok=True)
    trace_root.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    for key in (
        "OPENMINION_CONFIG",
        "OPENMINION_DATA_ROOT",
        "OPENMINION_IDENTITY_DB",
        "OPENMINION_IDENTITY_ROOT",
        "OPENMINION_TRACE_REQUESTS_DIR",
    ):
        env.pop(key, None)

    env["OPENMINION_HOME"] = str(framework_root())
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    env["OPENMINION_TRACE_REQUESTS"] = "1"
    env["OPENMINION_TRACE_REQUESTS_DIR"] = str(trace_root)
    if workspace_root_override is not None:
        workspace_root = str(workspace_root_override)
        env["OPENMINION_WORKSPACE_ROOT"] = workspace_root
        env["OPENMINION_WORKSPACE"] = workspace_root
    current_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    src_root = str(openminion_root() / "src")
    env["PYTHONPATH"] = (
        src_root
        if not current_pythonpath
        else f"{src_root}{os.pathsep}{current_pythonpath}"
    )
    resolved_agent_id = agent_id or default_agent_id()

    command = [
        str(resolved_python),
        "-m",
        "openminion",
        "--config",
        str(resolved_config),
        "chat",
        "--agent",
        resolved_agent_id,
        "--session",
        session_id,
        "--reset-session",
        "--quiet",
        "--no-progress",
    ]
    exit_code, transcript = _run_probe_session(
        cmd=command,
        cwd=str(workspace_root_override or openminion_root()),
        env=env,
        messages=[user_input.rstrip("\n")],
        timeout_seconds=float(timeout_seconds(matrix_type)),
        auto_confirm=auto_confirm,
    )
    transcript_path.write_text(transcript, encoding="utf-8")
    skip_if_provider_auth_rejected(
        transcript=transcript,
        transcript_path=transcript_path,
        context=f"live CLI chat E2E agent={resolved_agent_id}",
    )
    skip_if_provider_quota_rejected(
        transcript=transcript,
        transcript_path=transcript_path,
        context=f"live CLI chat E2E agent={resolved_agent_id}",
    )
    assert exit_code == 0, (
        f"cli chat failed for session={session_id} exit={exit_code}\n"
        f"transcript={transcript_path}\n"
        f"{transcript}"
    )
    return CLISessionResult(
        session_id=session_id,
        transcript=transcript,
        transcript_path=transcript_path,
        data_root=data_root,
        trace_root=trace_root,
    )
