#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[4]
OPENMINION_DIR = REPO_ROOT / "openminion"
OPENMINION_SRC = OPENMINION_DIR / "src"
if str(OPENMINION_DIR) not in sys.path:
    sys.path.insert(0, str(OPENMINION_DIR))
if str(OPENMINION_SRC) not in sys.path:
    sys.path.insert(0, str(OPENMINION_SRC))

from openminion.base.generated_paths import resolve_generated_root  # noqa: E402
from openminion.base.constants import (  # noqa: E402
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_GENERATED_ROOT_ENV,
)

AUTO_CONFIRM_LIMIT_ENV = "OPENMINION_LIVE_CLI_CHAT_AUTO_CONFIRM_LIMIT"
AUTO_CONFIRM_LIMIT_DEFAULT = 32
RUNNER_OWNED_COMMANDS = {"/debug", "/exit"}


def _open_probe_pty() -> tuple[int, int]:
    openpty = getattr(os, "openpty", None)
    if callable(openpty):
        return openpty()
    return pty.openpty()


def _auto_confirm_limit() -> int:
    raw = str(os.getenv(AUTO_CONFIRM_LIMIT_ENV, "")).strip()
    if not raw:
        return AUTO_CONFIRM_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return AUTO_CONFIRM_LIMIT_DEFAULT
    return max(value, 1)


RELEASE_GATE_CONVERSATION = (
    REPO_ROOT
    / "openminion"
    / "tests"
    / "e2e"
    / "fixtures"
    / "chat_permutations"
    / "e2e_chat_tool_calling_release_gate.txt"
)
EXTERNAL_SERVICES_CONVERSATION = (
    REPO_ROOT
    / "openminion"
    / "tests"
    / "e2e"
    / "fixtures"
    / "chat_permutations"
    / "e2e_chat_tool_calling_external_services.txt"
)
DIAGNOSTIC_CONVERSATION = (
    REPO_ROOT
    / "openminion"
    / "tests"
    / "e2e"
    / "fixtures"
    / "chat_permutations"
    / "e2e_chat_tool_calling_permutations.txt"
)
DEFAULT_CONVERSATIONS = [RELEASE_GATE_CONVERSATION]
EDGECASE_CONVERSATION = (
    REPO_ROOT
    / "openminion"
    / "tests"
    / "e2e"
    / "fixtures"
    / "chat_permutations"
    / "e2e_chat_tool_calling_edgecases.txt"
)
CHAOS_CONVERSATION = (
    REPO_ROOT
    / "openminion"
    / "tests"
    / "e2e"
    / "fixtures"
    / "chat_permutations"
    / "e2e_chat_tool_calling_chaos.txt"
)
DEFAULT_MODELS = {
    "echo": ["echo"],
    "minimax": ["MiniMax-M2.7"],
    "openai": ["gpt-4.1-mini"],
    "anthropic": ["claude-3-5-sonnet-latest"],
    "openrouter": ["openai/gpt-4.1-mini"],
    "cerebras": ["gpt-oss-120b"],
    "groq": ["llama-3.3-70b-versatile"],
    "ollama": ["llama3.1"],
    "cortensor": ["gpt-oss-20b"],
}

API_KEY_ENVS = {
    "minimax": "MINIMAX_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "groq": "GROQ_API_KEY",
    "cortensor": "CORTENSOR_API_KEY",
}
EXTERNAL_SERVICES_FIXTURES = {EXTERNAL_SERVICES_CONVERSATION.name}
TINYFISH_API_KEY_ENV = "TINYFISH_API_KEY"
PINCHTAB_SERVICE_ENVS = ("PINCHTAB_URL", "PINCHTAB_AUTOSTART")

PROVIDER_CONFIG_KEY = {
    "claude": "anthropic",
    "minimax": "openai",
}
PROVIDER_BASE_URLS = {
    "minimax": "https://api.minimax.io/v1",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_READY_PROMPT_RE = re.compile(r"(?:^|\n)(?:\[[^\]\n]+\]\s+you>|\u276f)\s*$")
_CONFIRMATION_REQUIRED_RE = re.compile(r"Policy confirmation required\.", re.IGNORECASE)
_INLINE_APPROVAL_RE = re.compile(
    r"(?:Approval required:[^\n]*\n)?\[y\]es / \[N\]o / \[a\]lways:\s*",
    re.IGNORECASE,
)
_TURN_DONE_RE = re.compile(r"(?:^|\n)Done in (?:\d+m)?\d+s\s*(?:\n|$)")
_KNOWN_FAILURE_RE = re.compile(
    r"General act work ended without the required typed "
    r"finalization_status contract|Adaptive loop stopped unexpectedly\.|"
    r"Denied by policy:|path escapes workspace root:",
    re.IGNORECASE,
)


def _default_artifacts_root() -> Path:
    return resolve_generated_root(home_root=OPENMINION_DIR) / "e2e"


def _default_log_root() -> Path:
    return _default_artifacts_root() / "chat-logs"


def _default_config_root() -> Path:
    return _default_artifacts_root() / "chat-configs"


def _default_work_root() -> Path:
    return _default_artifacts_root() / "chat-workdirs"


@dataclass
class RunResult:
    provider: str
    model: str
    scenario: str
    ok: bool
    skipped: bool
    reason: str
    log_path: Path


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", text.strip())
    return cleaned.strip("_") or "default"


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _provider_override_env_name(provider: str) -> str:
    return f"OPENMINION_E2E_API_KEY_ENV_{provider.upper()}"


def _provider_override_base_url_name(provider: str) -> str:
    return f"OPENMINION_E2E_BASE_URL_{provider.upper()}"


def _provider_override_config_name(provider: str) -> str:
    return f"OPENMINION_E2E_CONFIG_PATH_{provider.upper()}"


def _provider_override_agent_name(provider: str) -> str:
    return f"OPENMINION_E2E_AGENT_{provider.upper()}"


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _normalize_text(text: str) -> str:
    return _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")


def _ready_prompt_detected(text: str) -> bool:
    return bool(_READY_PROMPT_RE.search(_normalize_text(text)))


def _prompt_return_after_response_detected(text: str) -> bool:
    normalized = _normalize_text(text)
    if not _ready_prompt_detected(normalized):
        return False
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return False
    response_lines = [
        line
        for line in lines[:-1]
        if not re.match(r"^(?:\[[^\]\n]+\]\s+you>|\u276f)(?:\s+.*)?$", line)
    ]
    return bool(response_lines)


def _inline_approval_detected(text: str) -> bool:
    return bool(_INLINE_APPROVAL_RE.search(_normalize_text(text)))


def _text_delta(previous: str, current: str) -> str:
    normalized_current = _normalize_text(current)
    normalized_previous = _normalize_text(previous)
    if normalized_current.startswith(normalized_previous):
        return normalized_current[len(normalized_previous) :]
    return normalized_current


def _turn_response_boundary_detected_since(
    previous: str, current: str, *, require_turn_done: bool
) -> bool:
    delta = _text_delta(previous, current)
    if not delta:
        return False
    if "Queued messages" in delta:
        return False
    if _inline_approval_detected(delta):
        return True
    if _CONFIRMATION_REQUIRED_RE.search(delta) and _ready_prompt_detected(delta):
        return True
    if require_turn_done:
        return bool(_TURN_DONE_RE.search(delta))
    if _ready_prompt_detected(delta):
        return True
    return bool(_TURN_DONE_RE.search(delta)) or _prompt_return_after_response_detected(
        delta
    )


def _turn_response_or_confirmation_prompt_detected(
    previous: str, *, require_turn_done: bool
):
    def predicate(current: str) -> bool:
        return _turn_response_boundary_detected_since(
            previous, current, require_turn_done=require_turn_done
        ) or _latest_prompt_requires_confirmation(previous, current)

    return predicate


def _transcript_has_known_failure(text: str) -> bool:
    normalized = _normalize_text(text)
    if "[chat] turn failed" in normalized.lower():
        return True
    return bool(_KNOWN_FAILURE_RE.search(normalized))


def _latest_prompt_requires_confirmation(previous: str, current: str) -> bool:
    delta = _text_delta(previous, current)
    if not delta:
        return False
    return bool(
        (_CONFIRMATION_REQUIRED_RE.search(delta) and _ready_prompt_detected(delta))
        or _inline_approval_detected(delta)
    )


def _read_until(
    *,
    master_fd: int,
    transcript: list[str],
    timeout_seconds: int,
    predicate,
) -> str:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    combined = "".join(transcript)
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        ready, _, _ = select.select([master_fd], [], [], min(0.25, remaining))
        if not ready:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            continue
        except OSError:
            break
        if not chunk:
            break
        text = chunk.decode("utf-8", errors="replace")
        transcript.append(text)
        combined += text
        if predicate(combined):
            return combined
    raise TimeoutError("expected terminal boundary not reached before timeout")


def _read_until_prompt(
    *,
    master_fd: int,
    transcript: list[str],
    timeout_seconds: int,
) -> str:
    return _read_until(
        master_fd=master_fd,
        transcript=transcript,
        timeout_seconds=timeout_seconds,
        predicate=_ready_prompt_detected,
    )


def _resolve_providers() -> list[str]:
    providers = _env_list("OPENMINION_E2E_PROVIDERS")
    if providers:
        return providers
    return list(DEFAULT_MODELS.keys())


def _resolve_models(provider: str) -> list[str]:
    env_key = f"OPENMINION_E2E_MODELS_{provider.upper()}"
    models = _env_list(env_key)
    if models:
        return models
    if provider == "ollama":
        detected = _detect_ollama_models()
        if detected:
            return detected
    return DEFAULT_MODELS.get(provider, ["default"])


def _detect_ollama_models() -> list[str]:
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    models: list[str] = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            models.append(parts[0])
    return models


def _is_provider_available(provider: str) -> tuple[bool, str]:
    if provider == "echo":
        return True, "ok"
    override_config = os.getenv(_provider_override_config_name(provider), "").strip()
    if override_config:
        if Path(override_config).expanduser().resolve().exists():
            return True, "ok"
        return False, f"missing {_provider_override_config_name(provider)} target"
    if provider == "ollama":
        # If ollama is installed and running, allow; otherwise skip.
        try:
            proc = subprocess.run(
                ["ollama", "list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                return True, "ok"
        except FileNotFoundError:
            pass
        return False, "ollama not available (install or start server)"
    if provider in API_KEY_ENVS:
        env_name = (
            os.getenv(_provider_override_env_name(provider), "").strip()
            or API_KEY_ENVS[provider]
        )
        if os.getenv(env_name, "").strip():
            return True, "ok"
        return False, f"missing {env_name}"
    return True, "ok"


def _external_service_prerequisite_reason(conversation_path: Path) -> str:
    if conversation_path.name not in EXTERNAL_SERVICES_FIXTURES:
        return ""
    reasons = []
    if not os.getenv(TINYFISH_API_KEY_ENV, "").strip():
        reasons.append(f"missing_provider_key:{TINYFISH_API_KEY_ENV}")
    if not any(os.getenv(name, "").strip() for name in PINCHTAB_SERVICE_ENVS):
        joined = "/".join(PINCHTAB_SERVICE_ENVS)
        reasons.append(f"external_service_unavailable:{joined}")
    return "; ".join(reasons)


def _build_conversation(
    template_path: Path, _workdir: Path, *, skip_network: bool
) -> str:
    text = template_path.read_text(encoding="utf-8")
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.replace("{{WORKDIR}}", ".")
        if skip_network:
            lowered = line.lower()
            if "fetch http" in lowered or "https://" in lowered:
                continue
            if "weather" in lowered:
                continue
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _conversation_messages(conversation: str) -> list[str]:
    messages: list[str] = []
    for raw_line in conversation.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() in RUNNER_OWNED_COMMANDS:
            continue
        messages.append(line)
    return messages


def _conversation_workdir(
    *, work_root: Path, provider: str, model: str, scenario: str
) -> Path:
    return work_root / _slug(provider) / _slug(model) / _slug(scenario)


def _scenario_data_root_parent(log_root: Path) -> Path:
    return log_root.parent / "data-roots"


def _chat_subprocess_env(*, data_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(OPENMINION_DIR / "src")
    env["OPENMINION_HOME"] = str(OPENMINION_DIR)
    env[OPENMINION_DATA_ROOT_ENV] = str(data_root)
    env[OPENMINION_GENERATED_ROOT_ENV] = str(data_root / "runtime")
    return env


def _resolve_conversations(
    *,
    conversations: list[str] | None,
    conversation_dir: str | None,
    include_edgecases: bool,
    include_chaos: bool,
    include_external_services: bool,
    include_diagnostic: bool,
) -> list[Path]:
    if conversations:
        return [Path(item).expanduser().resolve() for item in conversations]

    if conversation_dir:
        root = Path(conversation_dir).expanduser().resolve()
        if not root.exists():
            return []
        return sorted(root.glob("*.txt"))

    resolved = list(DEFAULT_CONVERSATIONS)
    if include_external_services and EXTERNAL_SERVICES_CONVERSATION.exists():
        resolved.append(EXTERNAL_SERVICES_CONVERSATION)
    if include_diagnostic and DIAGNOSTIC_CONVERSATION.exists():
        resolved.append(DIAGNOSTIC_CONVERSATION)
    if include_edgecases and EDGECASE_CONVERSATION.exists():
        resolved.append(EDGECASE_CONVERSATION)
    if include_chaos and CHAOS_CONVERSATION.exists():
        resolved.append(CHAOS_CONVERSATION)
    return resolved


def _write_config(
    provider: str, model: str, config_path: Path, *, agent_id: str
) -> None:
    config: dict[str, object] = {
        "agents": {
            agent_id: {
                "default_channel": "console",
                "name": agent_id,
                "provider": PROVIDER_CONFIG_KEY.get(provider, provider),
                "system_prompt": "You are OpenMinion, a pragmatic assistant.",
            },
        },
        "default_agent": agent_id,
        "enabled_channels": ["console"],
        "runtime": {
            "demo_mode": provider == "echo",
            "process_mode": "single-process",
        },
    }
    if provider != "echo":
        config_key = PROVIDER_CONFIG_KEY.get(provider, provider)
        providers_payload = config.setdefault("providers", {})
        if isinstance(providers_payload, dict):
            provider_payload: dict[str, object] = {"model": model}
            api_key_env = os.getenv(
                _provider_override_env_name(provider), ""
            ).strip() or API_KEY_ENVS.get(provider, "")
            if api_key_env:
                provider_payload["api_key_env"] = api_key_env
            base_url = os.getenv(
                _provider_override_base_url_name(provider), ""
            ).strip() or PROVIDER_BASE_URLS.get(provider, "")
            if base_url:
                provider_payload["base_url"] = base_url
            providers_payload[config_key] = provider_payload
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run_chat(
    *,
    python_bin: Path,
    config_path: Path,
    data_root: Path,
    workdir: Path,
    agent_id: str,
    session_id: str,
    conversation: str,
    log_path: Path,
    timeout_seconds: int,
) -> tuple[bool, str]:
    env = _chat_subprocess_env(data_root=data_root)

    cmd = [
        str(python_bin),
        "-m",
        "openminion",
        "--config",
        str(config_path),
        "--data-root",
        str(data_root),
        "--generated-root",
        str(data_root / "runtime"),
        "--agent",
        agent_id,
        "--session",
        session_id,
        "--dir",
        str(workdir),
        "--verbosity",
        "quiet",
        "--progress",
        "off",
    ]
    master_fd, slave_fd = _open_probe_pty()
    proc = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(OPENMINION_DIR),
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    transcript: list[str] = []
    timeout_reason = ""
    return_code = 0
    has_ready = False
    try:
        try:
            _read_until_prompt(
                master_fd=master_fd,
                transcript=transcript,
                timeout_seconds=timeout_seconds,
            )
            has_ready = True
        except TimeoutError:
            timeout_reason = f"startup_timeout={timeout_seconds}"
        if not timeout_reason:
            for raw_message in _conversation_messages(conversation):
                previous_combined = "".join(transcript)
                os.write(master_fd, (raw_message + "\n").encode("utf-8"))
                require_turn_done = not raw_message.lstrip().startswith("/")
                try:
                    combined = _read_until(
                        master_fd=master_fd,
                        transcript=transcript,
                        timeout_seconds=timeout_seconds,
                        predicate=_turn_response_or_confirmation_prompt_detected(
                            previous_combined,
                            require_turn_done=require_turn_done,
                        ),
                    )
                except TimeoutError:
                    timeout_reason = f"turn_timeout={timeout_seconds}"
                    break
                confirmation_turns = 0
                confirmation_limit = _auto_confirm_limit()
                while _latest_prompt_requires_confirmation(previous_combined, combined):
                    confirmation_turns += 1
                    if confirmation_turns > confirmation_limit:
                        timeout_reason = "confirmation_loop_limit"
                        break
                    previous_combined = combined
                    os.write(master_fd, b"yes\n")
                    try:
                        combined = _read_until(
                            master_fd=master_fd,
                            transcript=transcript,
                            timeout_seconds=timeout_seconds,
                            predicate=_turn_response_or_confirmation_prompt_detected(
                                previous_combined,
                                require_turn_done=require_turn_done,
                            ),
                        )
                    except TimeoutError:
                        timeout_reason = f"confirmation_timeout={timeout_seconds}"
                        break
                if timeout_reason:
                    break
        try:
            os.write(master_fd, b"/exit\n")
        except OSError:
            pass
        try:
            proc.wait(timeout=max(5.0, min(30.0, timeout_seconds / 6.0)))
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        return_code = int(proc.returncode or 0)
    finally:
        combined = "".join(transcript)
        if combined:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(combined, encoding="utf-8")
        try:
            os.close(master_fd)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(combined, encoding="utf-8")

    known_failure = _transcript_has_known_failure(combined)
    ok = return_code == 0 and has_ready and not timeout_reason and not known_failure
    reason_parts = [
        f"returncode={return_code}",
        f"has_ready={has_ready}",
        f"known_failure={known_failure}",
    ]
    if timeout_reason:
        reason_parts.append(timeout_reason)
    reason = "; ".join(reason_parts)
    return ok, reason


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run long chat permutations across providers/models."
    )
    parser.add_argument(
        "--python", dest="python_bin", default=sys.executable, help="Python binary"
    )
    parser.add_argument(
        "--conversation",
        action="append",
        dest="conversations",
        default=None,
        help="Conversation template path (repeatable)",
    )
    parser.add_argument(
        "--conversation-dir",
        default="",
        help="Directory of .txt conversation templates",
    )
    parser.add_argument(
        "--include-edgecases",
        action="store_true",
        help="Include edge-case tool calling template",
    )
    parser.add_argument(
        "--include-chaos",
        action="store_true",
        help="Include chaos tool calling template",
    )
    parser.add_argument(
        "--include-external-services",
        action="store_true",
        help="Include opt-in search/browser/weather fixture with external prerequisites.",
    )
    parser.add_argument(
        "--include-diagnostic",
        action="store_true",
        help="Include the original broad diagnostic permutation fixture.",
    )
    parser.add_argument("--log-root", default="", help="Log output root")
    parser.add_argument("--config-root", default="", help="Config output root")
    parser.add_argument(
        "--work-root",
        default="",
        help="Scenario work directory root for in-chat file/tool operations.",
    )
    parser.add_argument(
        "--session-prefix", default="e2e-chat", help="Session id prefix"
    )
    parser.add_argument("--agent-prefix", default="e2e-agent", help="Agent id prefix")
    parser.add_argument(
        "--skip-network", action="store_true", help="Skip http/weather lines"
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=360,
        help="Per-conversation chat subprocess timeout in seconds.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    conversation_paths = _resolve_conversations(
        conversations=args.conversations,
        conversation_dir=args.conversation_dir or None,
        include_edgecases=args.include_edgecases
        or bool(os.getenv("OPENMINION_E2E_EDGECASES", "").strip()),
        include_chaos=args.include_chaos
        or bool(os.getenv("OPENMINION_E2E_CHAOS", "").strip()),
        include_external_services=args.include_external_services
        or bool(os.getenv("OPENMINION_E2E_EXTERNAL_SERVICES", "").strip()),
        include_diagnostic=args.include_diagnostic
        or bool(os.getenv("OPENMINION_E2E_DIAGNOSTIC", "").strip()),
    )
    if not conversation_paths:
        print("No conversation templates found.", file=sys.stderr)
        return 2

    providers = _resolve_providers()
    results: list[RunResult] = []
    log_root = Path(args.log_root or _default_log_root()).expanduser().resolve()
    config_root = (
        Path(args.config_root or _default_config_root()).expanduser().resolve()
    )
    work_root = Path(args.work_root or _default_work_root()).expanduser().resolve()
    data_root_parent = _scenario_data_root_parent(log_root).expanduser().resolve()
    data_root_parent.mkdir(parents=True, exist_ok=True)

    for provider in providers:
        available, reason = _is_provider_available(provider)
        if not available:
            results.append(
                RunResult(
                    provider=provider,
                    model="",
                    scenario="skipped",
                    ok=False,
                    skipped=True,
                    reason=reason,
                    log_path=log_root / f"{_slug(provider)}.log",
                )
            )
            continue

        models = _resolve_models(provider)
        for model in models:
            for conversation_path in conversation_paths:
                prerequisite_reason = _external_service_prerequisite_reason(
                    conversation_path
                )
                if prerequisite_reason:
                    results.append(
                        RunResult(
                            provider=provider,
                            model=model,
                            scenario=conversation_path.stem,
                            ok=False,
                            skipped=True,
                            reason=prerequisite_reason,
                            log_path=(
                                log_root / f"{_slug(provider)}--{_slug(model)}--"
                                f"{_slug(conversation_path.stem)}.log"
                            ),
                        )
                    )
                    continue
                data_root = Path(
                    tempfile.mkdtemp(
                        prefix=f"openminion-e2e-{provider}-",
                        dir=data_root_parent,
                    )
                )
                workdir = _conversation_workdir(
                    work_root=work_root,
                    provider=provider,
                    model=model,
                    scenario=conversation_path.stem,
                )
                workdir.mkdir(parents=True, exist_ok=True)
                conversation = _build_conversation(
                    conversation_path,
                    workdir,
                    skip_network=args.skip_network
                    or bool(os.getenv("OPENMINION_E2E_SKIP_NETWORK", "").strip()),
                )

                override_config = os.getenv(
                    _provider_override_config_name(provider), ""
                ).strip()
                agent_id = (
                    os.getenv(_provider_override_agent_name(provider), "").strip()
                    or f"{args.agent_prefix}-{_slug(provider)}"
                )
                if override_config:
                    config_path = Path(override_config).expanduser().resolve()
                else:
                    config_path = (
                        config_root / f"{_slug(provider)}--{_slug(model)}.json"
                    )
                    _write_config(provider, model, config_path, agent_id=agent_id)

                scenario = conversation_path.stem
                session_id = f"{args.session_prefix}-{_slug(provider)}-{_slug(model)}-{_slug(scenario)}"
                log_path = (
                    log_root
                    / f"{_slug(provider)}--{_slug(model)}--{_slug(scenario)}.log"
                )

                ok, run_reason = _run_chat(
                    python_bin=Path(args.python_bin),
                    config_path=config_path,
                    data_root=data_root,
                    workdir=workdir,
                    agent_id=agent_id,
                    session_id=session_id,
                    conversation=conversation,
                    log_path=log_path,
                    timeout_seconds=args.timeout_seconds,
                )
                results.append(
                    RunResult(
                        provider=provider,
                        model=model,
                        scenario=scenario,
                        ok=ok,
                        skipped=False,
                        reason=run_reason,
                        log_path=log_path,
                    )
                )

    summary = {
        "total": len(results),
        "passed": len([r for r in results if r.ok]),
        "skipped": len([r for r in results if r.skipped]),
        "failed": len([r for r in results if (not r.ok and not r.skipped)]),
        "results": [
            {
                "provider": r.provider,
                "model": r.model,
                "scenario": r.scenario,
                "ok": r.ok,
                "skipped": r.skipped,
                "reason": r.reason,
                "log": str(r.log_path),
            }
            for r in results
        ],
    }

    summary_path = log_root / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
