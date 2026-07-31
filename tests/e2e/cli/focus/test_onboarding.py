from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

import pytest

from openminion.base.config import (
    AgentProfileConfig,
    OpenMinionConfig,
    resolve_config_path,
)
from openminion.modules.llm.setup_catalog import get_setup_preset
from tests.e2e.cli.focus.harness import PtySession
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
        "PYTHONPATH": "src",
        "PYTHONDONTWRITEBYTECODE": "1",
        **overrides,
    }


def _reply(session: PtySession, prompt: str, answer: str = "") -> None:
    session.wait_for_after(prompt, offset=0, timeout=90)
    session.type_line(answer)


def _assert_owner_only(path: Path) -> None:
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


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
        _reply(session, "Choose your setup path:", "3")
        _reply(session, "OpenMinion config file:", str(import_path))
        _reply(session, r"Import this config\? \[Y/n\]:")
        session.wait_for_after(
            "Setup validation passed. Entering OpenMinion",
            offset=0,
            timeout=120,
        )
        session.wait_for_visible_match_after(_FOCUS_READY_RE, offset=0, timeout=120)
        transcript = session.transcript

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["default_agent"] == "imported"
    assert payload["agents"]["imported"]["provider"] == "echo"
    assert "Provider check skipped; no remote provider request was made." in transcript
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
        _reply(session, "Choose your setup path:", "1")
        _reply(session, "Choose your model service:", "1")
        _reply(session, "Model \\(press Enter for the existing or recommended default:")
        _reply(session, r"Create or repair this config\? \[Y/n\]:")
        _reply(session, r"Run provider check after doctor\? \[y/N\]:", "n")
        transcript = session.wait_for_after(
            "Setup complete. Interactive launch skipped",
            offset=0,
            timeout=120,
        )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["openminion"]["provider"] == "openai"
    assert payload["providers"]["openai"]["model"] == "gpt-4.1-mini"
    assert payload["providers"]["openai"]["api_key"] == ""
    assert "[recommended]" in transcript
    assert "Provider check skipped; no remote provider request was made." in transcript
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
        _reply(session, "Choose your setup path:", "2")
        _reply(session, "Model \\(press Enter for the existing or recommended default:")
        _reply(session, "Ollama base URL")
        _reply(session, r"Create or repair this config\? \[Y/n\]:", "n")
        transcript = session.wait_for_after(
            "Setup failed: Setup cancelled before writing config.",
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
        _reply(session, "Choose your setup path:", "1")
        _reply(session, "Choose your model service:", "1")
        _reply(session, "Model \\(press Enter for the existing or recommended default:")
        _reply(session, "OpenAI API key:")
        transcript = session.wait_for_after(
            "Export OPENAI_API_KEY and rerun setup.",
            offset=0,
            timeout=90,
        )

    assert not config_path.exists()
    write_transcript(artifact_root(tmp_path), "onboarding-missing-key", transcript)


def test_live_provider_setup_check(
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
            setup_only=True,
            extra_setup_args=("--provider", preset_id, "--check-provider"),
        ),
        cwd=openminion_root,
        env=_environment(
            home_root=home_root,
            data_root=data_root,
            **{preset.credential_env: credential},
        ),
    ) as session:
        transcript = session.wait_for_after(
            "Setup complete. Interactive launch skipped",
            offset=0,
            timeout=240,
        )

    assert "Provider check skipped" not in transcript
    assert "Provider check failed" not in transcript
    assert credential not in transcript
    assert credential not in config_path.read_text(encoding="utf-8")
    _assert_owner_only(config_path)
    write_transcript(artifact_root(tmp_path), "onboarding-live-provider", transcript)
