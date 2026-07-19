"""Slack controlplane CLI helpers."""

from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from openminion.base.config import OpenMinionConfig, resolve_config_path, save_config
from openminion.base.config.env import resolve_environment_config
from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.transport.daemon_client import (
    daemon_is_reachable,
    resolve_daemon_endpoint,
)
from openminion.modules.controlplane.channels.slack.bot_api import SlackWebAPI
from openminion.modules.controlplane.channels.slack.config import (
    from_base_config as slack_from_base_config,
    load_config as load_slack_config,
)
from openminion.modules.controlplane.config import (
    from_base_config as controlplane_from_base_config,
    load_config as load_controlplane_config,
)


SlackRunnerBuilder = Callable[[str | None], Any]


def run_slack_channel(
    args: argparse.Namespace,
    *,
    runner_builder: SlackRunnerBuilder | None = None,
) -> int:
    action = str(getattr(args, "slack_command", "") or "").strip().lower()
    handler = {
        "setup": slack_setup,
        "doctor": slack_doctor,
        "identify": slack_identify,
        "pair": slack_pair,
        "run": lambda value: slack_run(value, runner_builder=runner_builder),
        "status": slack_status,
    }.get(action)
    if handler is None:
        raise RuntimeError("unknown slack channel command")
    return handler(args)


def slack_setup(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(getattr(args, "config", None))
    bot_token, bot_config_value, bot_raw = _resolve_named_secret(
        args,
        ref_attr="bot_token_ref",
        stdin_attr="bot_token_stdin",
        unsafe_attr="unsafe_bot_token",
        prompt="Paste your Slack bot token (xoxb-): ",
    )
    app_token, app_config_value, app_raw = _resolve_named_secret(
        args,
        ref_attr="app_token_ref",
        stdin_attr="app_token_stdin",
        unsafe_attr="unsafe_app_token",
        prompt="Paste your Slack app token (xapp-), or press Enter to skip: ",
        optional=True,
    )
    signing_secret, signing_config_value, signing_raw = _resolve_named_secret(
        args,
        ref_attr="signing_secret_ref",
        stdin_attr="signing_secret_stdin",
        unsafe_attr="unsafe_signing_secret",
        prompt="Paste your Slack signing secret, or press Enter to skip: ",
        optional=True,
    )
    if (
        (bot_raw or app_raw or signing_raw)
        and _is_git_tracked(config_path)
        and not args.allow_tracked_secret
    ):
        print(
            "Refusing to write raw Slack secrets into a git-tracked config. "
            "Use env: references or pass --allow-tracked-secret."
        )
        return 2

    bot_info: dict[str, Any] | None = None
    if bot_token:
        try:
            bot_info = SlackWebAPI(bot_token).auth_test()
        except Exception as exc:
            print("Slack bot token could not be validated. Re-enter the token.")
            print(f"Validation error: {exc}")
            return 2

    config = (
        load_cli_config(str(config_path))
        if config_path.exists()
        else OpenMinionConfig()
    )
    _patch_slack_channel_config(
        config,
        bot_token_value=bot_config_value,
        app_token_value=app_config_value,
        signing_secret_value=signing_config_value,
    )
    save_config(config, str(config_path))
    print(f"Slack channel enabled in {config_path}")
    if bot_info:
        print(f"Bot user: {bot_info.get('user_id', '')}")
        print(f"Team: {bot_info.get('team', bot_info.get('team_id', ''))}")
    print("Tokens: [redacted]")
    print("Next: openminion channel slack doctor --config " + str(config_path))
    return 0


def slack_doctor(args: argparse.Namespace) -> int:
    checks = _slack_doctor_checks(args)
    if getattr(args, "json", False):
        print(json.dumps({"checks": checks}, indent=2, sort_keys=True))
    else:
        for check in checks:
            status = "ok" if check["ok"] else "fail"
            detail = f" - {check['detail']}" if check.get("detail") else ""
            print(f"[{status}] {check['id']}{detail}")
        print("Next: openminion channel slack run --config " + str(args.config))
    return 0 if all(bool(check["ok"]) for check in checks if check["required"]) else 1


def slack_identify(args: argparse.Namespace) -> int:
    cfg = _load_slack_channel_config(getattr(args, "config", None))
    print("Slack candidate discovery uses Socket Mode or a signed Events endpoint.")
    print(f"mode={cfg.mode}")
    print(
        "Send a DM or @mention to the Slack app while `openminion channel slack run` "
        "is online, then use the team/channel IDs from Slack event logs."
    )
    return 0


def slack_pair(args: argparse.Namespace) -> int:
    print(
        "Slack pairing is intentionally blocked until the cross-channel pairing "
        "core lands. This command will not create Slack-local pairing tokens."
    )
    print(
        "Tracker: docs/trackers/wip/controlplane-cross-channel-pairing-generalization-tracker.md"
    )
    return 2


def slack_run(
    args: argparse.Namespace,
    *,
    runner_builder: SlackRunnerBuilder | None = None,
) -> int:
    print(
        "Slack can control OpenMinion only while this runner is online. "
        "Keep this terminal open, or run OpenMinion as a daemon/service."
    )
    build_runner = runner_builder or _build_unified_slack_runner
    runner = build_runner(getattr(args, "config", None))
    stop = threading.Event()
    try:
        runner.start(stop_event=stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        stop_runner = getattr(runner, "stop", None)
        if callable(stop_runner):
            stop_runner()
    return 0


def slack_status(args: argparse.Namespace) -> int:
    config_path = getattr(args, "config", None)
    cfg = _load_slack_channel_config(config_path)
    cp_cfg = _load_controlplane_config(config_path)
    print(f"slack.enabled={cfg.enabled}")
    print(f"slack.mode={cfg.mode}")
    print(f"slack.state={cfg.state_sqlite_path}")
    print(f"controlplane.sqlite={cp_cfg.sqlite_path}")
    print(
        f"pairings.active={_count_active_channel_subjects(cp_cfg.sqlite_path, 'slack')}"
    )
    print(f"daemon.reachable={str(_daemon_reachable(config_path)).lower()}")
    print("daemon.state=not observed from this process")
    return 0


def register_slack_subcommands(
    channel_subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handler: Any,
) -> None:
    slack = channel_subcommands.add_parser(
        "slack", help="Slack channel setup, pairing, and status"
    )
    subcommands = slack.add_subparsers(dest="slack_command", required=True)

    setup = subcommands.add_parser("setup", help="Configure Slack")
    _add_config_arg(setup)
    setup.add_argument("--bot-token-stdin", action="store_true")
    setup.add_argument("--bot-token-ref", default=None)
    setup.add_argument("--unsafe-bot-token", default=None)
    setup.add_argument("--app-token-stdin", action="store_true")
    setup.add_argument("--app-token-ref", default=None)
    setup.add_argument("--unsafe-app-token", default=None)
    setup.add_argument("--signing-secret-stdin", action="store_true")
    setup.add_argument("--signing-secret-ref", default=None)
    setup.add_argument("--unsafe-signing-secret", default=None)
    setup.add_argument("--allow-tracked-secret", action="store_true")
    setup.set_defaults(handler=handler, needs_app=False)

    doctor = subcommands.add_parser("doctor", help="Check Slack setup")
    _add_config_arg(doctor)
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=handler, needs_app=False)

    identify = subcommands.add_parser("identify", help="Show Slack ID guidance")
    _add_config_arg(identify)
    identify.set_defaults(handler=handler, needs_app=False)

    pair = subcommands.add_parser("pair", help="Create a Slack pairing token")
    _add_config_arg(pair)
    pair.add_argument("--team-id", default=None)
    pair.add_argument("--channel-id", default=None)
    pair.set_defaults(handler=handler, needs_app=False)

    run = subcommands.add_parser("run", help="Run the Slack channel")
    _add_config_arg(run)
    run.set_defaults(handler=handler, needs_app=False)

    status = subcommands.add_parser("status", help="Show Slack status")
    _add_config_arg(status)
    status.set_defaults(handler=handler, needs_app=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openminion-controlplane-slack")
    direct = parser.add_subparsers(dest="slack_command", required=True)
    _register_direct(direct)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return run_slack_channel(args)


def _register_direct(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    for name, help_text in {
        "setup": "Configure Slack",
        "doctor": "Check Slack setup",
        "identify": "Show Slack ID guidance",
        "pair": "Create a Slack pairing token",
        "run": "Run the Slack channel",
        "status": "Show Slack status",
    }.items():
        parser = subcommands.add_parser(name, help=help_text)
        _add_config_arg(parser)
        if name == "setup":
            parser.add_argument("--bot-token-stdin", action="store_true")
            parser.add_argument("--bot-token-ref", default=None)
            parser.add_argument("--unsafe-bot-token", default=None)
            parser.add_argument("--app-token-stdin", action="store_true")
            parser.add_argument("--app-token-ref", default=None)
            parser.add_argument("--unsafe-app-token", default=None)
            parser.add_argument("--signing-secret-stdin", action="store_true")
            parser.add_argument("--signing-secret-ref", default=None)
            parser.add_argument("--unsafe-signing-secret", default=None)
            parser.add_argument("--allow-tracked-secret", action="store_true")
        if name == "doctor":
            parser.add_argument("--json", action="store_true")
        if name == "pair":
            parser.add_argument("--team-id", default=None)
            parser.add_argument("--channel-id", default=None)


def _resolve_named_secret(
    args: argparse.Namespace,
    *,
    ref_attr: str,
    stdin_attr: str,
    unsafe_attr: str,
    prompt: str,
    optional: bool = False,
) -> tuple[str, str, bool]:
    ref = str(getattr(args, ref_attr, "") or "").strip()
    if ref:
        if not ref.startswith("env:") or not ref[4:].strip():
            raise RuntimeError(f"--{ref_attr.replace('_', '-')} must use env:NAME")
        name = ref[4:].strip()
        return _env_snapshot().get(name, ""), f"${{{name}}}", False
    if bool(getattr(args, stdin_attr, False)):
        value = sys.stdin.readline().strip()
        return value, value, bool(value)
    unsafe = str(getattr(args, unsafe_attr, "") or "").strip()
    if unsafe:
        return unsafe, unsafe, True
    if optional and not sys.stdin.isatty():
        return "", "", False
    value = getpass.getpass(prompt).strip()
    return value, value, bool(value)


def _patch_slack_channel_config(
    config: Any,
    *,
    bot_token_value: str,
    app_token_value: str,
    signing_secret_value: str,
) -> None:
    enabled = list(getattr(config, "enabled_channels", []) or [])
    if "slack" not in enabled:
        enabled.append("slack")
    config.enabled_channels = enabled
    channels = dict(getattr(config, "channels", {}) or {})
    slack = dict(channels.get("slack") or {})
    slack["enabled"] = True
    slack.setdefault("mode", "socket")
    if bot_token_value:
        slack["botToken"] = bot_token_value
    if app_token_value:
        slack["appToken"] = app_token_value
    if signing_secret_value:
        slack["signingSecret"] = signing_secret_value
    slack.setdefault("access", {"requirePairing": True})
    slack.setdefault("pairing", {"enabled": True, "mode": "required"})
    channels["slack"] = slack
    config.channels = channels


def _load_slack_channel_config(config_path: str | None):
    base = load_cli_config(config_path)
    if "slack" in dict(getattr(base, "channels", {}) or {}):
        roots = resolve_cli_roots(config_path=config_path)
        return slack_from_base_config(
            base_config=base,
            home_root=roots.home_root,
            data_root=roots.data_root,
        ).slack
    return load_slack_config(config_path, env=_env_snapshot()).slack


def _load_controlplane_config(config_path: str | None):
    base = load_cli_config(config_path)
    if "controlplane" in dict(getattr(base, "channels", {}) or {}):
        roots = resolve_cli_roots(config_path=config_path)
        return controlplane_from_base_config(
            base_config=base,
            home_root=roots.home_root,
            data_root=roots.data_root,
        )
    return load_controlplane_config(config_path, env=_env_snapshot())


def _build_unified_slack_runner(config_path: str | None):
    from openminion.cli.commands.channel import (
        build_unified_slack_runner as build_runner,
    )

    return build_runner(config_path)


def _slack_doctor_checks(args: argparse.Namespace) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    config_path = getattr(args, "config", None)
    try:
        cfg = _load_slack_channel_config(config_path)
        checks.append(_check("config.parse", True, "config parsed"))
    except Exception as exc:
        return [_check("config.parse", False, str(exc))]
    cp_cfg = _load_controlplane_config(config_path)
    checks.append(
        _check("channel.enabled", bool(cfg.enabled), "channels.slack.enabled")
    )
    bot_present = bool(cfg.bot_token)
    app_present = bool(cfg.app_token)
    checks.append(
        _check(
            "bot_token.present",
            bot_present,
            "token=[redacted]" if bot_present else "missing",
        )
    )
    checks.append(
        _check(
            "app_token.present",
            app_present,
            "token=[redacted]" if app_present else "missing",
            required=False,
        )
    )
    if bot_present:
        try:
            auth = SlackWebAPI(cfg.bot_token).auth_test()
            checks.append(
                _check("bot.auth_test", True, str(auth.get("user_id") or "ok"))
            )
        except Exception as exc:
            checks.append(_check("bot.auth_test", False, str(exc)))
    else:
        checks.append(_check("bot.auth_test", False, "missing token"))
    checks.append(
        _check(
            "state.writable",
            _path_parent_writable(cfg.state_sqlite_path),
            cfg.state_sqlite_path,
        )
    )
    checks.append(
        _check(
            "controlplane.writable",
            _path_parent_writable(cp_cfg.sqlite_path),
            cp_cfg.sqlite_path,
        )
    )
    checks.append(_check("transport.mode", True, cfg.mode, required=False))
    checks.append(_check("pairing.mode", False, "blocked on CCP", required=False))
    checks.append(
        _check(
            "pairings.active",
            True,
            str(_count_active_channel_subjects(cp_cfg.sqlite_path, "slack")),
            required=False,
        )
    )
    checks.append(
        _check(
            "daemon.reachable",
            _daemon_reachable(config_path),
            "runner/daemon status",
            required=False,
        )
    )
    return checks


def _check(
    check_id: str, ok: bool, detail: str = "", *, required: bool = True
) -> dict[str, Any]:
    return {"id": check_id, "ok": bool(ok), "detail": detail, "required": required}


def _path_parent_writable(raw_path: str) -> bool:
    path = Path(raw_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".openminion-write-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _count_active_channel_subjects(sqlite_path: str, channel: str) -> int:
    path = Path(sqlite_path).expanduser()
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM cp_channel_subjects
                WHERE lower(channel) = lower(?) AND lower(status) = 'active'
                """,
                (channel,),
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] if row else 0)


def _daemon_reachable(config_path: str | None) -> bool:
    try:
        endpoint = resolve_daemon_endpoint(config_path)
        return daemon_is_reachable(endpoint)
    except Exception:
        return False


def _is_git_tracked(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _env_snapshot() -> dict[str, str]:
    return resolve_environment_config().snapshot()


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Config file path")


if __name__ == "__main__":
    raise SystemExit(main())
