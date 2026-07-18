from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.commands import channel
from openminion.cli.parser.base import build_parser


class FakeTelegramBotAPI:
    updates: list[dict] = []
    fail_get_me = False
    command_syncs: list[list[dict[str, str]]] = []

    def __init__(self, token: str):
        self.token = token

    def get_me(self) -> dict:
        if self.fail_get_me or self.token == "bad-token":
            raise RuntimeError("invalid token")
        return {"id": 123, "username": "openminion_test_bot"}

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        limit: int,
        allowed_updates: list[str],
    ) -> list[dict]:
        return list(self.updates)

    def set_my_commands(self, commands: list[dict[str, str]]) -> dict:
        self.command_syncs.append(list(commands))
        return {"value": True}


def _write_profile(tmp_path: Path, *, token: str = "good-token") -> Path:
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled_channels": ["console", "telegram"],
                "channels": {
                    "controlplane": {
                        "sqlite_path": "controlplane/cp.db",
                        "openminion_enabled": False,
                    },
                    "telegram": {
                        "enabled": True,
                        "botToken": token,
                        "mode": "polling",
                        "polling": {
                            "stateSqlitePath": "controlplane/telegram-poll-state.db"
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _setup_args(config_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config=str(config_path),
        bot_token_stdin=True,
        bot_token_file=None,
        bot_token_ref=None,
        unsafe_bot_token=None,
        allow_tracked_secret=False,
    )


def _pair_args(config_path: Path, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "config": str(config_path),
        "user_id": None,
        "chat_id": None,
        "ttl_seconds": 60,
        "scopes": None,
        "wait": True,
        "timeout_seconds": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_channel_telegram_subcommands_registered() -> None:
    parser = build_parser()
    args = parser.parse_args(["channel", "telegram", "setup", "--config", "a.json"])
    assert args.command == "channel"
    assert args.channel_name == "telegram"
    assert args.telegram_command == "setup"
    assert args.config == "a.json"

    sync_args = parser.parse_args(
        ["channel", "telegram", "commands-sync", "--config", "a.json"]
    )
    assert sync_args.telegram_command == "commands-sync"


def test_setup_writes_unified_config_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    monkeypatch.setattr("sys.stdin", io.StringIO("good-token\n"))
    config_path = tmp_path / "agent.json"

    rc = channel.telegram_setup(_setup_args(config_path))

    assert rc == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert "telegram" in payload["enabled_channels"]
    assert payload["channels"]["telegram"]["enabled"] is True
    assert payload["channels"]["telegram"]["botToken"] == "good-token"
    assert "good-token" not in capsys.readouterr().out


def test_setup_stdin_reads_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    monkeypatch.setattr("sys.stdin", io.StringIO("good-token\nignored-extra\n"))
    config_path = tmp_path / "agent.json"

    rc = channel.telegram_setup(_setup_args(config_path))

    assert rc == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["botToken"] == "good-token"


def test_setup_invalid_token_does_not_write_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    monkeypatch.setattr("sys.stdin", io.StringIO("bad-token\n"))
    config_path = tmp_path / "agent.json"
    original = '{"enabled_channels": ["console"], "channels": {}}\n'
    config_path.write_text(original, encoding="utf-8")

    rc = channel.telegram_setup(_setup_args(config_path))

    assert rc == 2
    assert config_path.read_text(encoding="utf-8") == original


def test_setup_refuses_raw_token_in_tracked_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    monkeypatch.setattr("sys.stdin", io.StringIO("good-token\n"))
    config_path = tmp_path / "agent.json"
    config_path.write_text('{"enabled_channels": ["console"], "channels": {}}\n')
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "agent.json"], cwd=tmp_path, check=True)

    rc = channel.telegram_setup(_setup_args(config_path))

    assert rc == 2
    assert "good-token" not in config_path.read_text(encoding="utf-8")


def test_doctor_reports_json_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    config_path = _write_profile(tmp_path)

    rc = channel.telegram_doctor(SimpleNamespace(config=str(config_path), json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    check_ids = {check["id"] for check in payload["checks"]}
    assert {"config.parse", "bot.get_me", "pairings.active"} <= check_ids


def test_identify_prints_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    monkeypatch.setattr(channel, "_daemon_reachable", lambda _path: False)
    FakeTelegramBotAPI.updates = [
        {
            "update_id": 7,
            "message": {
                "message_id": 1,
                "from": {"id": 11, "username": "alice", "first_name": "Alice"},
                "chat": {"id": 22, "type": "private"},
                "text": "hello",
            },
        }
    ]
    config_path = _write_profile(tmp_path)

    rc = channel.telegram_identify(
        SimpleNamespace(config=str(config_path), timeout_seconds=0)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "user_id: 11" in output
    assert "chat_id: 22" in output


def test_identify_reports_active_runner_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_profile(tmp_path)
    monkeypatch.setattr(channel, "_daemon_reachable", lambda _path: True)

    rc = channel.telegram_identify(
        SimpleNamespace(config=str(config_path), timeout_seconds=0)
    )

    assert rc == 1
    output = capsys.readouterr().out
    assert "getUpdates" in output
    assert "--user-id" in output


def test_pair_known_id_prints_deep_link_and_access_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    config_path = _write_profile(tmp_path)

    rc = channel.telegram_pair(
        _pair_args(config_path, user_id=11, chat_id=22, wait=False)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "PAIR_TOKEN=" in output
    assert "PAIR_DEEP_LINK=https://t.me/openminion_test_bot?start=" in output
    assert "broad non-admin controlplane access" in output


def test_pair_wait_confirms_candidate_before_issuing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    monkeypatch.setattr(channel, "_daemon_reachable", lambda _path: False)
    monkeypatch.setattr(channel, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        channel,
        "_discover_telegram_candidate",
        lambda **_kwargs: channel.TelegramCandidate(
            update_id=1,
            user_id=11,
            chat_id=22,
            chat_type="private",
            username="alice",
            display_name="Alice",
        ),
    )
    config_path = _write_profile(tmp_path)

    rc = channel.telegram_pair(_pair_args(config_path))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Telegram candidate found:" in output
    assert "PAIR_TOKEN=" in output


def test_pair_wait_reports_active_runner_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_profile(tmp_path)
    monkeypatch.setattr(channel, "_daemon_reachable", lambda _path: True)

    rc = channel.telegram_pair(_pair_args(config_path))

    assert rc == 1
    output = capsys.readouterr().out
    assert "getUpdates" in output
    assert "--user-id" in output


def test_run_starts_unified_profile_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Runner:
        def __init__(self) -> None:
            self.run_once_calls = 0

        def run_once(self) -> int:
            self.run_once_calls += 1
            return 0

    runner = _Runner()
    config_path = _write_profile(tmp_path)
    foreground = SimpleNamespace(
        runner=runner,
        outbox_worker=None,
        stop=lambda: None,
    )
    monkeypatch.setattr(
        channel, "_build_unified_telegram_runtime", lambda _config_path: foreground
    )

    rc = channel.telegram_run(SimpleNamespace(config=str(config_path), once=True))

    assert rc == 0
    assert runner.run_once_calls == 1
    assert "runner is online" in capsys.readouterr().out


def test_status_prints_runner_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_profile(tmp_path)
    monkeypatch.setattr(channel, "daemon_is_reachable", lambda _endpoint: False)

    rc = channel.telegram_status(SimpleNamespace(config=str(config_path)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "telegram.enabled=True" in output
    assert "daemon.state=not observed from this process" in output
    assert "runner is online" in output


def test_commands_sync_updates_bot_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FakeTelegramBotAPI.command_syncs = []
    monkeypatch.setattr(channel, "TelegramBotAPI", FakeTelegramBotAPI)
    config_path = _write_profile(tmp_path)

    rc = channel.telegram_commands_sync(SimpleNamespace(config=str(config_path)))

    assert rc == 0
    assert FakeTelegramBotAPI.command_syncs
    commands = {item["command"] for item in FakeTelegramBotAPI.command_syncs[-1]}
    assert {"help", "status", "new", "profile", "pair"} <= commands
    assert "Synced" in capsys.readouterr().out
