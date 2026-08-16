from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Iterator

import pytest

from openminion.base.config import (
    AgentProfileConfig,
    OpenMinionConfig,
    resolve_config_path,
)
from openminion.modules.llm.setup_catalog import get_setup_preset
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario, PtySession
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(240)]

_FOCUS_READY_RE = re.compile(
    r"Ask anything|Reply, or / for commands|input:\s*(?:send|queue next) message|"
    r"(?:^|\n)\s*❯\s*\Z"
)


def _command(
    *,
    python_bin: Path,
    config_path: Path | None,
    home_root: Path,
    data_root: Path,
    setup_only: bool,
    extra_setup_args: tuple[str, ...] = (),
) -> tuple[str, ...]:
    command: tuple[str, ...] = (
        str(python_bin),
        "-m",
        "openminion",
        "--home-root",
        str(home_root),
        "--data-root",
        str(data_root),
        "--no-update-check",
    )
    if config_path is not None:
        command += ("--config", str(config_path))
    if setup_only:
        command += ("setup", "--no-focus", *extra_setup_args)
    elif extra_setup_args:
        command += ("setup", *extra_setup_args)
    return command


def _environment(
    *,
    home_root: Path,
    data_root: Path,
    **overrides: str,
) -> dict[str, str]:
    return {
        "OPENMINION_HOME": str(home_root),
        "OPENMINION_DATA_ROOT": str(data_root),
        "OPENMINION_GENERATED_ROOT": str(data_root / "runtime"),
        "PYTHONPATH": "src",
        "PYTHONDONTWRITEBYTECODE": "1",
        **overrides,
    }


def _reply(session: PtySession, prompt: str, answer: str = "") -> None:
    session.wait_for_after(prompt, offset=0, timeout=90)
    session.type_line(answer)


def _run_first_task(
    session: PtySession,
    *,
    python_bin: Path,
    openminion_root: Path,
    data_root: Path,
    config_path: Path,
    timeout: int,
) -> str:
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=openminion_root.parent,
        data_root=data_root,
        config_path=config_path,
        agent_id="openminion",
        workdir=openminion_root,
        session_id="onboarding-first-run",
        include_project_context=True,
    )
    probe.wait_ready(session)
    return probe.run_turn(
        session,
        FocusScenario(
            scenario_id="onboarding-first-task",
            prompt=(
                "In one sentence, give me one safe read-only command to inspect "
                "the current directory and end with exactly: ONBOARDING_OK"
            ),
            expected_markers=("ONBOARDING_OK", "ls|find|Get-ChildItem"),
            timeout=timeout,
        ),
    )


def _assert_owner_only(path: Path) -> None:
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


class _OllamaFixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        request_payload = {}
        if content_length:
            request_payload = json.loads(self.rfile.read(content_length))
        messages = request_payload.get("messages", [])
        last_message = messages[-1] if isinstance(messages, list) and messages else {}
        prompt = str(last_message.get("content", "") or "").lower()
        response_text = "openminion provider check ok"
        if "openminion provider check ok" not in prompt:
            response_text = "Use ls to inspect the current directory. ONBOARDING_OK"
        payload = json.dumps(
            {
                "model": "qwen2.5:14b",
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def _ollama_fixture_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_noninteractive_setup_case(
    *,
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    name: str,
    preset_id: str,
    base_url: str,
    model: str,
    credential_env: str,
    credential: str,
) -> tuple[dict[str, Any], str]:
    home_root = tmp_path / f"{name}-home"
    data_root = tmp_path / f"{name}-data"
    config_path = home_root / ".openminion" / "config.json"
    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
            extra_setup_args=(
                "--provider",
                preset_id,
                "--api-format",
                "openai-compatible",
                "--base-url",
                base_url,
                "--model",
                model,
            ),
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            **{credential_env: credential},
        ),
    ) as session:
        transcript = session.wait_for_after(
            "Interactive launch skipped",
            offset=0,
            timeout=120,
        )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    _assert_owner_only(config_path)
    return payload, transcript


def _write_import_source(path: Path) -> None:
    config = OpenMinionConfig()
    config.agents = {
        "imported": AgentProfileConfig(
            name="imported",
            provider="echo",
            default_channel="console",
        )
    }
    config.default_agent = "imported"
    config.runtime.demo_mode = True
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    path.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_bare_command_imports_config_and_reaches_focus(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = resolve_config_path(None, home_root=home_root)
    import_path = tmp_path / "existing-openminion.json"
    _write_import_source(import_path)

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=None,
            home_root=home_root,
            data_root=data_root,
            setup_only=False,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
    ) as session:
        _reply(session, "Choose your model provider:", "8")
        _reply(session, "OpenMinion config file:", str(import_path))
        _reply(session, r"Import this config\? \[Y/n\]:")
        session.wait_for_after(
            "Entering OpenMinion",
            offset=0,
            timeout=120,
        )
        session.wait_for_visible_match_after(_FOCUS_READY_RE, offset=0, timeout=120)
        transcript = session.transcript

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["default_agent"] == "imported"
    assert payload["agents"]["imported"]["provider"] == "echo"
    assert "Provider connection not applicable for this setup path." in transcript
    _assert_owner_only(config_path)
    write_transcript(artifact_root(tmp_path), "onboarding-import", transcript)


def test_hosted_setup_uses_env_and_skips_remote_check(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"
    fixture_key = "fixture-openai-key-not-for-network-use"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            OPENAI_API_KEY=fixture_key,
        ),
    ) as session:
        _reply(session, "Choose your model provider:", "1")
        _reply(session, "Model \\[")
        _reply(session, r"Save this configuration\? \[Y/n\]:")
        _reply(session, r"Test this provider now\? \[y/N\]:", "n")
        transcript = session.wait_for_after(
            "Interactive launch skipped",
            offset=0,
            timeout=120,
        )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["openminion"]["provider"] == "openai"
    assert payload["providers"]["openai"]["model"] == "gpt-4.1-mini"
    assert payload["providers"]["openai"]["api_key"] == ""
    assert "[recommended]" in transcript
    assert "provider: OpenAI" in transcript
    assert "runtime adapter:" not in transcript
    assert "Connection not tested; no provider request was made." in transcript
    assert fixture_key not in transcript
    assert fixture_key not in config_path.read_text(encoding="utf-8")
    _assert_owner_only(config_path)
    write_transcript(artifact_root(tmp_path), "onboarding-hosted", transcript)


def test_local_setup_is_keyless_and_cancellable(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
    ) as session:
        _reply(session, "Choose your model provider:", "6")
        _reply(session, "Model \\[")
        _reply(session, "Ollama base URL")
        _reply(session, r"Save this configuration\? \[Y/n\]:", "n")
        transcript = session.wait_for_after(
            "Setup cancelled; configuration not written.",
            offset=0,
            timeout=90,
        )

    assert "API key" not in transcript
    assert not config_path.exists()
    write_transcript(artifact_root(tmp_path), "onboarding-local-cancel", transcript)


def test_missing_hosted_credential_cancels_without_writing(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            OPENAI_API_KEY="",
        ),
    ) as session:
        _reply(session, "Choose your model provider:", "1")
        _reply(session, "Model \\[")
        _reply(
            session,
            "Store a key in the owner-readable local OpenMinion config",
            "n",
        )
        transcript = session.wait_for_after(
            "Export OPENAI_API_KEY and rerun setup.",
            offset=0,
            timeout=90,
        )

    assert not config_path.exists()
    assert "Get or manage a key: https://platform.openai.com/api-keys" in transcript
    write_transcript(artifact_root(tmp_path), "onboarding-missing-key", transcript)


def test_hosted_more_menu_back_and_cancel_stays_readable_at_80_columns(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
        cols=80,
    ) as session:
        _reply(session, "Choose your model provider:", "7")
        _reply(session, "Choose another provider or custom endpoint:", "b")
        _reply(session, "Choose your model provider:", "7")
        _reply(session, "Choose another provider or custom endpoint:", "c")
        transcript = session.wait_for_after(
            "Setup cancelled; configuration not written.",
            offset=0,
            timeout=90,
        )

    assert "b. Back" in transcript
    assert "c. Cancel setup" in transcript
    assert "Traceback" not in transcript
    assert not config_path.exists()
    write_transcript(artifact_root(tmp_path), "onboarding-more-back-cancel", transcript)


def test_hosted_minimax_setup_lists_all_recommended_models(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"
    fixture_key = "fixture-minimax-key-not-for-network-use"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            MINIMAX_API_KEY=fixture_key,
        ),
    ) as session:
        _reply(session, "Choose your model provider:", "5")
        _reply(session, "Choose a recommended model", "2")
        _reply(session, r"Save this configuration\? \[Y/n\]:")
        _reply(session, r"Test this provider now\? \[y/N\]:", "n")
        transcript = session.wait_for_after(
            "Interactive launch skipped",
            offset=0,
            timeout=120,
        )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["openminion"]["provider"] == "openai"
    assert payload["providers"]["openai"]["model"] == "MiniMax-M2.7-highspeed"
    assert "MiniMax-M2.7 (recommended)" in transcript
    assert "MiniMax-M2.7-highspeed (recommended)" in transcript
    assert "model: MiniMax-M2.7-highspeed [recommended]" in transcript
    assert fixture_key not in transcript
    assert fixture_key not in config_path.read_text(encoding="utf-8")
    _assert_owner_only(config_path)
    write_transcript(artifact_root(tmp_path), "onboarding-minimax-models", transcript)


def test_local_ollama_check_failure_does_not_claim_readiness(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
    ) as session:
        _reply(session, "Choose your model provider:", "6")
        _reply(session, "Model \\[")
        _reply(session, "Ollama base URL", "http://127.0.0.1:1")
        _reply(session, r"Save this configuration\? \[Y/n\]:")
        _reply(session, r"Test Ollama now\? \[Y/n\]:", "y")
        transcript = session.wait_for_after(
            "Connection check failed",
            offset=0,
            timeout=120,
        )

    assert config_path.exists()
    assert "Connection verified." not in transcript
    assert "Interactive launch skipped" not in transcript
    _assert_owner_only(config_path)
    write_transcript(
        artifact_root(tmp_path),
        "onboarding-ollama-check-fails",
        transcript,
    )


def test_local_ollama_check_can_be_declined_after_config_is_saved(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
    ) as session:
        _reply(session, "Choose your model provider:", "6")
        _reply(session, "Model \\[")
        _reply(session, "Ollama base URL")
        _reply(session, r"Save this configuration\? \[Y/n\]:")
        _reply(session, r"Test Ollama now\? \[Y/n\]:", "n")
        transcript = session.wait_for_after(
            "Interactive launch skipped",
            offset=0,
            timeout=120,
        )

    assert config_path.exists()
    assert "Connection not tested; no provider request was made." in transcript
    assert "Setup ready" not in transcript
    assert "Setup complete" not in transcript
    _assert_owner_only(config_path)
    write_transcript(
        artifact_root(tmp_path),
        "onboarding-ollama-check-declined",
        transcript,
    )


def test_local_ollama_check_can_verify_against_fixture_server(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = resolve_config_path(None, home_root=home_root)

    with _ollama_fixture_server() as base_url:
        with PtySession(
            argv=_command(
                python_bin=python_bin,
                config_path=None,
                home_root=home_root,
                data_root=data_root,
                setup_only=False,
            ),
            cwd=openminion_root,
            env=_environment(home_root=home_root, data_root=data_root),
        ) as session:
            _reply(session, "Choose your model provider:", "6")
            _reply(
                session,
                "Model \\[",
                "qwen2.5:14b",
            )
            _reply(session, "Ollama base URL", base_url)
            _reply(session, r"Save this configuration\? \[Y/n\]:")
            _reply(session, r"Test Ollama now\? \[Y/n\]:", "y")
            session.wait_for_after("Entering OpenMinion", offset=0, timeout=120)
            first_task = _run_first_task(
                session,
                python_bin=python_bin,
                openminion_root=openminion_root,
                data_root=data_root,
                config_path=config_path,
                timeout=120,
            )
            transcript = session.transcript

    assert "Connection verified." in transcript
    assert "Connection not tested" not in transcript
    assert "Connection check failed" not in transcript
    assert "ONBOARDING_OK" in first_task
    _assert_owner_only(config_path)
    write_transcript(
        artifact_root(tmp_path),
        "onboarding-ollama-check-verified",
        transcript,
    )


@pytest.mark.parametrize(
    ("control", "case_name"),
    (("\x03", "ctrl-c"), ("\x04", "eof")),
)
def test_setup_cancellation_before_write_is_clean(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    control: str,
    case_name: str,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
    ) as session:
        session.wait_for_after("Choose your model provider:", offset=0, timeout=90)
        session.send(control)
        transcript = session.wait_for_after(
            "Setup cancelled; configuration not written.",
            offset=0,
            timeout=90,
        )

    assert "Traceback" not in transcript
    assert not config_path.exists()
    write_transcript(
        artifact_root(tmp_path),
        f"onboarding-{case_name}-before-write",
        transcript,
    )


@pytest.mark.parametrize(
    ("control", "case_name"),
    (("\x03", "ctrl-c"), ("\x04", "eof")),
)
def test_setup_cancellation_after_write_preserves_saved_truth(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    control: str,
    case_name: str,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
    ) as session:
        _reply(session, "Choose your model provider:", "6")
        _reply(session, "Model \\[")
        _reply(session, "Ollama base URL")
        _reply(session, r"Save this configuration\? \[Y/n\]:")
        session.wait_for_after(
            r"Test Ollama now\? \[Y/n\]:",
            offset=0,
            timeout=120,
        )
        session.send(control)
        transcript = session.wait_for_after(
            "Setup cancelled after configuration was saved",
            offset=0,
            timeout=90,
        )

    assert config_path.exists()
    assert "connection not tested" in transcript
    assert "Traceback" not in transcript
    _assert_owner_only(config_path)
    write_transcript(
        artifact_root(tmp_path),
        f"onboarding-{case_name}-after-write",
        transcript,
    )


def test_setup_repairs_shared_adapter_without_changing_existing_agent(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"
    config_path.parent.mkdir(parents=True)
    config = OpenMinionConfig()
    config.agents = {
        "existing-openai": AgentProfileConfig(
            name="existing-openai",
            provider="openai",
            default_channel="console",
        )
    }
    config.default_agent = "existing-openai"
    config.providers.openai.model = "gpt-4.1-mini"
    config.providers.openai.base_url = "https://api.openai.com/v1"
    config.runtime.demo_mode = False
    config_path.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    fixture_key = "fixture-minimax-key-not-for-network-use"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
            extra_setup_args=("--agent", "minimax-agent"),
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            MINIMAX_API_KEY=fixture_key,
        ),
    ) as session:
        _reply(session, "Choose your model provider:", "5")
        _reply(session, "Choose a recommended model", "1")
        _reply(session, r"Save this configuration\? \[Y/n\]:")
        _reply(session, r"Test this provider now\? \[y/N\]:", "n")
        transcript = session.wait_for_after(
            "Interactive launch skipped",
            offset=0,
            timeout=120,
        )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["existing-openai"]["provider"] == "openai"
    assert payload["providers"]["openai"]["model"] == "gpt-4.1-mini"
    assert (
        payload["agents"]["minimax-agent"]["provider_config_overrides"]["model"]
        == "MiniMax-M2.7"
    )
    assert "repair: existing agents remain unchanged" in transcript
    assert "provider: MiniMax" in transcript
    assert "runtime adapter:" not in transcript
    assert fixture_key not in transcript
    assert fixture_key not in config_path.read_text(encoding="utf-8")
    _assert_owner_only(config_path)
    write_transcript(artifact_root(tmp_path), "onboarding-repair", transcript)


@pytest.mark.parametrize("cols", (80, 120))
def test_provider_listing_is_width_safe(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    cols: int,
) -> None:
    home_root = tmp_path / f"home-{cols}"
    data_root = tmp_path / f"data-{cols}"

    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=None,
            home_root=home_root,
            data_root=data_root,
            setup_only=True,
            extra_setup_args=("--list-providers",),
        ),
        cwd=openminion_root,
        env=_environment(home_root=home_root, data_root=data_root),
        cols=cols,
    ) as session:
        transcript = session.wait_for_after(
            "Custom endpoints:",
            offset=0,
            timeout=90,
        )

    lines = [line.rstrip("\r") for line in transcript.splitlines()]
    assert max(len(line) for line in lines) <= 80
    assert "minimax: MiniMax" in transcript
    assert "API format: openai-compatible" in transcript
    assert "Credential: MINIMAX_API_KEY" in transcript
    assert "Endpoint: https://api.minimax.io/v1" in transcript
    assert "Recommended models: MiniMax-M2.7" in transcript
    write_transcript(
        artifact_root(tmp_path),
        f"onboarding-provider-list-{cols}",
        transcript,
    )


def test_noninteractive_openai_compatible_setups_preserve_api_format(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    dashscope_url = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
    dashscope_payload, dashscope_transcript = _run_noninteractive_setup_case(
        tmp_path=tmp_path,
        python_bin=python_bin,
        openminion_root=openminion_root,
        name="dashscope",
        preset_id="qwen-dashscope",
        base_url=dashscope_url,
        model="qwen3.7-plus",
        credential_env="DASHSCOPE_API_KEY",
        credential="fixture-dashscope-key-not-for-network-use",
    )
    assert dashscope_payload["agents"]["openminion"]["provider"] == "openai"
    assert dashscope_payload["providers"]["openai"]["base_url"] == dashscope_url
    assert dashscope_payload["providers"]["openai"]["provider_identity"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "dashscope",
        "model_family": "qwen",
    }
    assert "Connection not tested; no provider request was made." in (
        dashscope_transcript
    )
    assert "fixture-dashscope-key" not in dashscope_transcript

    custom_url = "https://models.example.invalid/v1"
    custom_payload, custom_transcript = _run_noninteractive_setup_case(
        tmp_path=tmp_path,
        python_bin=python_bin,
        openminion_root=openminion_root,
        name="custom",
        preset_id="custom-openai-compatible",
        base_url=custom_url,
        model="vendor-model",
        credential_env="OPENAI_COMPATIBLE_API_KEY",
        credential="fixture-custom-key-not-for-network-use",
    )
    assert custom_payload["agents"]["openminion"]["provider"] == "openai"
    assert custom_payload["providers"]["openai"]["base_url"] == custom_url
    assert custom_payload["providers"]["openai"]["provider_identity"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "openai",
        "model_family": "openai",
    }
    assert "Connection not tested; no provider request was made." in (custom_transcript)
    assert "fixture-custom-key" not in custom_transcript
    write_transcript(
        artifact_root(tmp_path),
        "onboarding-openai-compatible-api-format",
        dashscope_transcript + "\n--- custom ---\n" + custom_transcript,
    )


def test_live_provider_setup_and_first_task(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
) -> None:
    if str(os.getenv("OPENMINION_LIVE_CLI_FOCUS_E2E", "")).strip() != "1":
        pytest.skip("live onboarding proof requires explicit live E2E consent")

    preset_id = str(os.getenv("OPENMINION_ONBOARDING_E2E_PROVIDER", "minimax")).strip()
    preset = get_setup_preset(preset_id)
    credential = (
        str(os.getenv(preset.credential_env, "")).strip()
        if preset.credential_env
        else ""
    )
    if not preset.credential_env or not credential:
        pytest.skip(f"{preset.credential_env or 'provider credential'} is unavailable")

    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    config_path = home_root / ".openminion" / "config.json"
    with PtySession(
        argv=_command(
            python_bin=python_bin,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            setup_only=False,
            extra_setup_args=("--provider", preset_id, "--check-provider"),
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            **{preset.credential_env: credential},
        ),
    ) as session:
        session.wait_for_after("Entering OpenMinion", offset=0, timeout=240)
        first_task = _run_first_task(
            session,
            python_bin=python_bin,
            openminion_root=openminion_root,
            data_root=data_root,
            config_path=config_path,
            timeout=240,
        )
        transcript = session.transcript

    assert "Connection not tested" not in transcript
    assert "Connection check failed" not in transcript
    assert "ONBOARDING_OK" in first_task
    assert credential not in transcript
    assert credential not in config_path.read_text(encoding="utf-8")
    _assert_owner_only(config_path)
    write_transcript(artifact_root(tmp_path), "onboarding-live-provider", transcript)
