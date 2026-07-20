from __future__ import annotations

import argparse
import getpass
import json
import logging
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openminion.base.config import (
    OpenMinionConfig,
    resolve_config_path,
    save_config,
)
from openminion.base.config.env import resolve_environment_config
from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.transport.daemon_client import (
    probe_daemon_endpoint,
    resolve_daemon_endpoint,
)
from openminion.cli.commands.telegram_status import (
    build_telegram_status_payload,
)
from openminion.cli.commands.channel_pairings import (
    register_pairings_subcommands,
    run_channel_pairings,
)
from openminion.modules.controlplane.config import (
    ControlPlaneConfig,
    from_base_config as controlplane_from_base_config,
    load_config as load_controlplane_config,
)
from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.config import (
    TelegramChannelConfig,
    from_base_config as telegram_from_base_config,
    load_config as load_telegram_config,
)
from openminion.modules.controlplane.channels.telegram.cli import (
    issue_pair_token_for_cli,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)


RUNNER_ONLINE_MESSAGE = (
    "Telegram can control OpenMinion only while this runner is online. "
    "Keep this terminal open, or run OpenMinion as a daemon/service."
)

TELEGRAM_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "help", "description": "Show OpenMinion commands"},
    {
        "command": "status",
        "description": "Show connection, profile, and session status",
    },
    {"command": "new", "description": "Start a fresh session"},
    {"command": "sessions", "description": "List sessions for this chat"},
    {"command": "profile", "description": "List or switch runtime profiles"},
    {"command": "pair", "description": "Show pairing status"},
]


@dataclass(frozen=True)
class PairTokenOutput:
    token: str
    token_hint: str
    token_hash_prefix: str
    expires_at_iso: str
    scopes: list[str]
    deep_link: str | None


@dataclass(frozen=True)
class TelegramCandidate:
    update_id: int
    user_id: int
    chat_id: int
    chat_type: str
    username: str
    display_name: str


@dataclass
class _ForegroundChannelRuntime:
    runner: Any
    supervisor: Any
    registry: Any
    outbox_worker: Any | None = None

    def start(self, stop_event: threading.Event | None = None) -> None:
        event = stop_event or threading.Event()
        self.supervisor.start()
        while not event.is_set():
            event.wait(0.2)

    def stop(self) -> None:
        self.supervisor.stop()


def run_channel(args: argparse.Namespace) -> int:
    channel = str(getattr(args, "channel_name", "") or "").strip().lower()
    if channel == "pairings":
        return run_channel_pairings(args)
    if channel == "telegram":
        action = str(getattr(args, "telegram_command", "") or "").strip().lower()
        handler = {
            "setup": telegram_setup,
            "doctor": telegram_doctor,
            "identify": telegram_identify,
            "pair": telegram_pair,
            "run": telegram_run,
            "status": telegram_status,
            "commands-sync": telegram_commands_sync,
        }.get(action)
        if handler is None:
            raise RuntimeError("unknown telegram channel command")
        return handler(args)
    if channel == "slack":
        from openminion.modules.controlplane.channels.slack.cli import (
            run_slack_channel,
        )

        return run_slack_channel(args, runner_builder=build_unified_slack_runner)
    raise RuntimeError("unknown channel command")


def telegram_setup(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(getattr(args, "config", None))
    token_value, config_value, raw_secret = _resolve_setup_token(args)
    bot_info: dict[str, Any] | None = None
    if token_value:
        try:
            bot_info = TelegramBotAPI(token_value).get_me()
        except Exception as exc:
            print(
                "Telegram bot token could not be validated. "
                "Re-enter the token or run doctor after fixing the token reference."
            )
            print(f"Validation error: {exc}")
            return 2

    if raw_secret and _is_git_tracked(config_path) and not args.allow_tracked_secret:
        print(
            "Refusing to write a raw Telegram bot token into a git-tracked config. "
            "Use --bot-token-ref env:TELEGRAM_BOT_TOKEN or pass --allow-tracked-secret."
        )
        return 2

    config = (
        load_cli_config(str(config_path))
        if config_path.exists()
        else OpenMinionConfig()
    )
    _patch_telegram_channel_config(config, bot_token_value=config_value)
    save_config(config, str(config_path))

    username = str((bot_info or {}).get("username") or "").strip()
    print(f"Telegram channel enabled in {config_path}")
    if username:
        print(f"Bot: @{username}")
    print("Token: [redacted]")
    print("Next: openminion channel telegram doctor --config " + str(config_path))
    return 0


def telegram_doctor(args: argparse.Namespace) -> int:
    checks = _telegram_doctor_checks(args)
    if getattr(args, "json", False):
        print(json.dumps({"checks": checks}, indent=2, sort_keys=True))
    else:
        for check in checks:
            status = "ok" if check["ok"] else "fail"
            detail = f" - {check['detail']}" if check.get("detail") else ""
            print(f"[{status}] {check['id']}{detail}")
        print("Next: openminion channel telegram identify --config " + str(args.config))
    return 0 if all(bool(check["ok"]) for check in checks if check["required"]) else 1


def telegram_identify(args: argparse.Namespace) -> int:
    config_path = getattr(args, "config", None)
    if _daemon_reachable(config_path):
        _print_get_updates_conflict("Telegram identify")
        return 1
    candidate = _discover_telegram_candidate(
        config_path=config_path,
        timeout_seconds=_timeout_seconds(args),
    )
    if candidate is None:
        print("No Telegram messages found. Send a DM to the bot and retry.")
        return 1
    _print_candidate(candidate)
    return 0


def telegram_pair(args: argparse.Namespace) -> int:
    if bool(getattr(args, "wait", False)):
        return _telegram_pair_wait(args)
    user_id = getattr(args, "user_id", None)
    chat_id = getattr(args, "chat_id", None)
    if user_id is None and chat_id is None:
        print("Usage: openminion channel telegram pair --user-id <id> --chat-id <id>")
        return 2
    output = create_telegram_pair_token_for_cli(
        config_path=getattr(args, "config", None),
        user_id=user_id,
        chat_id=chat_id,
        ttl_seconds=getattr(args, "ttl_seconds", None),
        scopes=_parse_scopes(getattr(args, "scopes", None)),
    )
    print_pair_token_output(output)
    return 0


def telegram_run(args: argparse.Namespace) -> int:
    print(RUNNER_ONLINE_MESSAGE)
    foreground = _build_unified_telegram_runtime(getattr(args, "config", None))
    runner = foreground.runner
    if bool(getattr(args, "once", False)):
        try:
            run_once = getattr(runner, "run_once", None)
            if not callable(run_once):
                raise SystemExit("--once is only supported in polling mode")
            run_once()
            if foreground.outbox_worker is not None:
                foreground.outbox_worker.run_once()
        finally:
            foreground.stop()
        return 0

    stop = threading.Event()
    try:
        foreground.start(stop_event=stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        foreground.stop()
    return 0


def telegram_status(args: argparse.Namespace) -> int:
    payload = _telegram_status_payload(args)
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    telegram_payload = payload["telegram"]
    controlplane_payload = payload["controlplane"]
    daemon_payload = payload["daemon"]
    session_payload = payload["session"]
    print(f"telegram.enabled={telegram_payload['enabled']}")
    print(f"telegram.mode={telegram_payload['mode']}")
    print(f"telegram.poll_state={telegram_payload['poll_state']}")
    print(f"controlplane.sqlite={controlplane_payload['sqlite_path']}")
    print(f"default.profile={controlplane_payload['default_profile']}")
    print(f"pairings.active={payload['pairings']['active']}")
    print(f"daemon.reachable={str(daemon_payload['reachable']).lower()}")
    print(f"daemon.endpoint_status={daemon_payload['endpoint_status']}")
    print(f"daemon.state={daemon_payload['state']}")
    print(f"telegram.listener_state={telegram_payload['listener_state']}")
    print(f"telegram.listener_alive={telegram_payload['listener_alive']}")
    print(f"telegram.connected={telegram_payload['connected']}")
    if session_payload["chat_key"]:
        print(f"session.chat_key={session_payload['chat_key']}")
    print(f"active.session={session_payload['session_id']}")
    print(f"active.profile={session_payload['profile_id']}")
    print(RUNNER_ONLINE_MESSAGE)
    return 0


def _telegram_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = getattr(args, "config", None)
    cfg = _load_telegram_channel_config(config_path)
    cp_cfg = _load_controlplane_config(config_path)
    probe_status, health_payload = _daemon_probe(config_path)
    return build_telegram_status_payload(
        telegram_config=cfg,
        controlplane_config=cp_cfg,
        daemon_probe_status=probe_status,
        daemon_payload=health_payload,
        active_pairings=_count_active_pairings(cp_cfg.sqlite_path),
        chat_id=getattr(args, "chat_id", None),
        topic_id=getattr(args, "topic_id", None),
    )


def telegram_commands_sync(args: argparse.Namespace) -> int:
    cfg = _load_telegram_channel_config(getattr(args, "config", None))
    if not cfg.enabled:
        print("channels.telegram.enabled is false")
        return 1
    api = TelegramBotAPI(cfg.bot_token)
    api.set_my_commands(TELEGRAM_BOT_COMMANDS)
    print(f"Synced {len(TELEGRAM_BOT_COMMANDS)} Telegram bot commands.")
    print("Open the bot menu in Telegram or send /help.")
    return 0


def create_telegram_pair_token_for_cli(
    *,
    config_path: str | None,
    user_id: int | str | None,
    chat_id: int | str | None,
    ttl_seconds: int | None = None,
    scopes: list[str] | None = None,
) -> PairTokenOutput:
    cfg = _load_telegram_channel_config(config_path)
    if not cfg.enabled:
        raise RuntimeError("channels.telegram.enabled is false")
    if user_id is None and chat_id is None:
        raise RuntimeError("pair-create requires --user-id and/or --chat-id")
    selected_scopes = scopes or list(cfg.pairing.default_scopes)
    _issued_cfg, issued = issue_pair_token_for_cli(
        config_path=config_path,
        user_id=user_id,
        chat_id=chat_id,
        ttl_seconds=ttl_seconds or cfg.pairing.token_ttl_seconds,
        scopes=selected_scopes,
    )
    expires_iso = datetime.fromtimestamp(
        issued.expires_at_ts, tz=timezone.utc
    ).isoformat()
    deep_link = None
    if cfg.bot_token:
        try:
            me = TelegramBotAPI(cfg.bot_token).get_me()
            username = str(me.get("username") or "").strip()
            if username:
                deep_link = f"https://t.me/{username}?start={issued.token}"
        except Exception:
            deep_link = None
    return PairTokenOutput(
        token=issued.token,
        token_hint=issued.token_hint,
        token_hash_prefix=issued.token_hash_prefix,
        expires_at_iso=expires_iso,
        scopes=list(issued.scopes),
        deep_link=deep_link,
    )


def create_telegram_pair_token_from_chat_line(
    *, line: str, config: object
) -> PairTokenOutput:
    parts = line.split()
    user_id: str | None = None
    chat_id: str | None = None
    ttl_seconds: int | None = None
    scopes: list[str] = []
    i = 2
    while i < len(parts):
        if parts[i] == "--user-id" and i + 1 < len(parts):
            user_id = parts[i + 1]
            i += 2
            continue
        if parts[i] == "--chat-id" and i + 1 < len(parts):
            chat_id = parts[i + 1]
            i += 2
            continue
        if parts[i] == "--ttl-seconds" and i + 1 < len(parts):
            ttl_seconds = int(parts[i + 1])
            i += 2
            continue
        if parts[i] == "--scopes" and i + 1 < len(parts):
            scopes = _parse_scopes(parts[i + 1])
            i += 2
            continue
        i += 1
    config_path = getattr(config, "telegram_config_path", None) or getattr(
        config, "config_path", None
    )
    return create_telegram_pair_token_for_cli(
        config_path=str(config_path) if config_path else None,
        user_id=user_id,
        chat_id=chat_id,
        ttl_seconds=ttl_seconds,
        scopes=scopes,
    )


def print_pair_token_output(output: PairTokenOutput) -> None:
    print("Pairing token created.")
    print(f"PAIR_TOKEN={output.token}")
    print(f"PAIR_TOKEN_HINT={output.token_hint}")
    print(f"PAIR_TOKEN_HASH_PREFIX={output.token_hash_prefix}")
    print(f"PAIR_EXPIRES_AT={output.expires_at_iso}")
    print(f"PAIR_SCOPES={','.join(output.scopes)}")
    if output.deep_link:
        print(f"PAIR_DEEP_LINK={output.deep_link}")
        print("Open this link:")
        print(output.deep_link)
    print("Or send this message to the bot:")
    print(f"/start {output.token}")
    print(
        "Access: this paired Telegram chat receives broad non-admin controlplane "
        "access until a future ACL system narrows it."
    )


def _telegram_pair_wait(args: argparse.Namespace) -> int:
    config_path = getattr(args, "config", None)
    if _daemon_reachable(config_path):
        _print_get_updates_conflict("Guided pairing")
        return 1
    candidate = _discover_telegram_candidate(
        config_path=config_path,
        timeout_seconds=_timeout_seconds(args),
    )
    if candidate is None:
        print("No Telegram messages found. Send a DM to the bot and retry.")
        return 1
    _print_candidate(candidate)
    if candidate.chat_type in {"group", "supergroup"}:
        print("Warning: group pairing grants access to the room, not just one person.")
    if not _confirm("Create a constrained pairing token for this chat?", default=False):
        print("Pairing cancelled.")
        return 1
    output = create_telegram_pair_token_for_cli(
        config_path=config_path,
        user_id=candidate.user_id,
        chat_id=candidate.chat_id,
        ttl_seconds=getattr(args, "ttl_seconds", None),
        scopes=_parse_scopes(getattr(args, "scopes", None)),
    )
    print_pair_token_output(output)
    return 0


def _telegram_doctor_checks(args: argparse.Namespace) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    config_path = getattr(args, "config", None)
    try:
        cfg = _load_telegram_channel_config(config_path)
        checks.append(_check("config.parse", True, "config parsed"))
    except Exception as exc:
        return [_check("config.parse", False, str(exc))]
    cp_cfg = _load_controlplane_config(config_path)
    checks.append(
        _check("channel.enabled", bool(cfg.enabled), "channels.telegram.enabled")
    )
    token_present = bool(str(cfg.bot_token or "").strip())
    checks.append(
        _check(
            "token.present",
            token_present,
            "token=[redacted]" if token_present else "missing",
        )
    )
    if token_present:
        try:
            me = TelegramBotAPI(cfg.bot_token).get_me()
            checks.append(
                _check("bot.get_me", True, f"@{me.get('username', '')}".rstrip("@"))
            )
        except Exception as exc:
            checks.append(_check("bot.get_me", False, str(exc)))
    else:
        checks.append(_check("bot.get_me", False, "missing token"))
    checks.append(
        _check(
            "poll_state.writable",
            _path_parent_writable(cfg.polling.state_sqlite_path),
            cfg.polling.state_sqlite_path,
        )
    )
    checks.append(
        _check(
            "controlplane.writable",
            _path_parent_writable(cp_cfg.sqlite_path),
            cp_cfg.sqlite_path,
        )
    )
    checks.append(_check("pairing.mode", True, cfg.pairing.mode, required=False))
    checks.append(_check("transport.mode", True, cfg.mode, required=False))
    webhook_ok = (not cfg.webhook.enabled) or bool(
        str(cfg.webhook.secret or "").strip()
    )
    checks.append(_check("webhook.secret", webhook_ok, "required when webhook enabled"))
    checks.append(
        _check(
            "pairings.active",
            True,
            str(_count_active_pairings(cp_cfg.sqlite_path)),
            required=False,
        )
    )
    status_payload = _telegram_status_payload(args)
    _append_telegram_status_checks(checks, status_payload)
    return checks


def _append_telegram_status_checks(
    checks: list[dict[str, Any]], status_payload: dict[str, Any]
) -> None:
    checks.append(
        _check(
            "default.profile",
            True,
            status_payload["controlplane"]["default_profile"],
            required=False,
        )
    )
    session_payload = status_payload["session"]
    session_detail = (
        f"{session_payload['session_id']} profile={session_payload['profile_id']}"
        if session_payload["chat_key"]
        else "pass --chat-id to inspect active session"
    )
    checks.append(
        _check(
            "session.binding",
            session_payload["session_id"] not in {"not_found", "not_observed"},
            session_detail,
            required=False,
        )
    )
    daemon_ok = bool(status_payload["daemon"]["reachable"])
    checks.append(
        _check("daemon.reachable", daemon_ok, "runner/daemon status", required=False)
    )
    checks.append(
        _check(
            "daemon.state",
            True,
            status_payload["daemon"]["state"],
            required=False,
        )
    )
    checks.append(
        _check(
            "telegram.listener",
            True,
            status_payload["telegram"]["listener_state"],
            required=False,
        )
    )


def _check(
    check_id: str, ok: bool, detail: str = "", *, required: bool = True
) -> dict[str, Any]:
    return {"id": check_id, "ok": bool(ok), "detail": detail, "required": required}


def _resolve_setup_token(args: argparse.Namespace) -> tuple[str, str, bool]:
    ref = str(getattr(args, "bot_token_ref", "") or "").strip()
    if ref:
        if not ref.startswith("env:") or not ref[4:].strip():
            raise RuntimeError("--bot-token-ref must use env:NAME")
        name = ref[4:].strip()
        return _env_snapshot().get(name, ""), f"${{{name}}}", False
    file_path = str(getattr(args, "bot_token_file", "") or "").strip()
    if file_path:
        token = Path(file_path).expanduser().read_text(encoding="utf-8").strip()
        return token, token, True
    if bool(getattr(args, "bot_token_stdin", False)):
        token = sys.stdin.readline().strip()
        return token, token, True
    unsafe = str(getattr(args, "unsafe_bot_token", "") or "").strip()
    if unsafe:
        return unsafe, unsafe, True
    token = getpass.getpass("Paste your BotFather token: ").strip()
    return token, token, True


def _patch_telegram_channel_config(config: Any, *, bot_token_value: str) -> None:
    enabled = list(getattr(config, "enabled_channels", []) or [])
    if "telegram" not in enabled:
        enabled.append("telegram")
    config.enabled_channels = enabled
    channels = dict(getattr(config, "channels", {}) or {})
    telegram = dict(channels.get("telegram") or {})
    telegram["enabled"] = True
    telegram.setdefault("mode", "polling")
    telegram["botToken"] = bot_token_value
    telegram.setdefault("pairing", {"enabled": True, "mode": "dm"})
    telegram.setdefault("polling", {})
    channels["telegram"] = telegram
    config.channels = channels


def _discover_telegram_candidate(
    *, config_path: str | None, timeout_seconds: int
) -> TelegramCandidate | None:
    cfg = _load_telegram_channel_config(config_path)
    api = TelegramBotAPI(cfg.bot_token)
    store = TelegramPollStateStore(cfg.polling.state_sqlite_path)
    account_id = _telegram_account_id(api)
    lease = store.acquire_polling_lease(
        account_id=account_id,
        command="openminion channel telegram identify",
        stale_after_seconds=30,
    )
    if not lease.acquired:
        raise RuntimeError(lease.diagnostic())
    try:
        print("Send a message to your Telegram bot now. Waiting for an update...")
        deadline = time.time() + max(0, int(timeout_seconds))
        while True:
            store.heartbeat_polling_lease(account_id=account_id)
            updates = api.get_updates(
                offset=None,
                timeout=min(2, max(0, timeout_seconds)),
                limit=10,
                allowed_updates=["message", "edited_message"],
            )
            for update in updates:
                candidate = _candidate_from_update(update)
                if candidate is not None:
                    return candidate
            if time.time() >= deadline:
                return None
            time.sleep(0.2)
    finally:
        store.release_polling_lease(account_id=account_id)


def _telegram_account_id(api: TelegramBotAPI) -> str:
    me = api.get_me()
    bot_id = str(me.get("id") or "").strip()
    return f"telegram-bot:{bot_id}" if bot_id else "default"


def _candidate_from_update(update: dict[str, Any]) -> TelegramCandidate | None:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    user = message.get("from")
    chat = message.get("chat")
    if not isinstance(user, dict) or not isinstance(chat, dict):
        return None
    try:
        user_id = int(user["id"])
        chat_id = int(chat["id"])
        update_id = int(update.get("update_id") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    username = str(user.get("username") or "").strip()
    display_parts = [
        str(user.get("first_name") or "").strip(),
        str(user.get("last_name") or "").strip(),
    ]
    display_name = " ".join(part for part in display_parts if part).strip()
    return TelegramCandidate(
        update_id=update_id,
        user_id=user_id,
        chat_id=chat_id,
        chat_type=str(chat.get("type") or "unknown"),
        username=username,
        display_name=display_name,
    )


def _print_candidate(candidate: TelegramCandidate) -> None:
    print("Telegram candidate found:")
    print(f"  user_id: {candidate.user_id}")
    print(f"  chat_id: {candidate.chat_id}")
    print(f"  chat_type: {candidate.chat_type}")
    if candidate.username:
        print(f"  username: @{candidate.username}")
    if candidate.display_name:
        print(f"  display_name: {candidate.display_name}")


def _confirm(prompt: str, *, default: bool) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    if not sys.stdin.isatty():
        return default
    answer = input(prompt + suffix + " ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _parse_scopes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [scope for item in raw.split(",") if (scope := item.strip())]


def _load_telegram_channel_config(config_path: str | None) -> TelegramChannelConfig:
    base = load_cli_config(config_path)
    if "telegram" in dict(getattr(base, "channels", {}) or {}):
        roots = resolve_cli_roots(config_path=config_path)
        return telegram_from_base_config(
            base_config=base,
            home_root=roots.home_root,
            data_root=roots.data_root,
        ).telegram
    return load_telegram_config(config_path, env=_env_snapshot()).telegram


def _load_controlplane_config(config_path: str | None) -> ControlPlaneConfig:
    base = load_cli_config(config_path)
    if "controlplane" in dict(getattr(base, "channels", {}) or {}):
        roots = resolve_cli_roots(config_path=config_path)
        return controlplane_from_base_config(
            base_config=base,
            home_root=roots.home_root,
            data_root=roots.data_root,
        )
    return load_controlplane_config(config_path, env=_env_snapshot())


def _build_unified_telegram_runner(
    config_path: str | None,
) -> Any:
    return _build_unified_telegram_runtime(config_path).runner


def _build_unified_telegram_runtime(
    config_path: str | None,
) -> _ForegroundChannelRuntime:
    base = load_cli_config(config_path)
    roots = resolve_cli_roots(config_path=config_path)
    return _build_unified_telegram_runtime_from_base(
        base=base,
        home_root=roots.home_root,
        data_root=roots.data_root,
        logger_name="openminion.cli.channel.telegram",
    )


def _build_unified_telegram_runtime_from_base(
    *,
    base: Any,
    home_root: Path,
    data_root: Path,
    logger_name: str,
) -> _ForegroundChannelRuntime:
    from openminion.services.runtime.lifecycle import (
        build_channel_registry as lifecycle_build_channel_registry,
    )
    from openminion.services.runtime.channel_supervisor import ChannelRuntimeSupervisor

    registry, components = lifecycle_build_channel_registry(
        config=base,
        home_root=home_root,
        data_root=data_root,
        logger=logging.getLogger(logger_name),
    )
    if components is None:
        raise RuntimeError("telegram channel requires controlplane runtime components")
    return _ForegroundChannelRuntime(
        runner=registry.get("telegram"),
        registry=registry,
        outbox_worker=components.outbox_worker,
        supervisor=ChannelRuntimeSupervisor(
            channels=registry,
            outbox_worker=components.outbox_worker,
            close_runtime=components.close,
            logger=logging.getLogger(f"{logger_name}.supervisor"),
            channel_ids=["telegram"],
        ),
    )


def _build_controlplane_components_from_base(
    *,
    base: Any,
    home_root: Path,
    data_root: Path,
    logger_name: str,
) -> Any:
    from openminion.services.runtime.controlplane import (
        build_controlplane_runtime_components,
    )

    return build_controlplane_runtime_components(
        config=base,
        home_root=home_root,
        data_root=data_root,
        logger=logging.getLogger(logger_name),
    )


def build_unified_slack_runner(config_path: str | None) -> _ForegroundChannelRuntime:
    from openminion.services.runtime.lifecycle import (
        build_channel_registry as lifecycle_build_channel_registry,
    )
    from openminion.services.runtime.channel_supervisor import ChannelRuntimeSupervisor

    base = load_cli_config(config_path)
    roots = resolve_cli_roots(config_path=config_path)
    registry, components = lifecycle_build_channel_registry(
        config=base,
        home_root=roots.home_root,
        data_root=roots.data_root,
        logger=logging.getLogger("openminion.cli.channel.slack"),
    )
    if components is None:
        raise RuntimeError("slack channel requires controlplane runtime components")
    return _ForegroundChannelRuntime(
        runner=registry.get("slack"),
        registry=registry,
        outbox_worker=components.outbox_worker,
        supervisor=ChannelRuntimeSupervisor(
            channels=registry,
            outbox_worker=components.outbox_worker,
            close_runtime=components.close,
            logger=logging.getLogger("openminion.cli.channel.slack.supervisor"),
            channel_ids=["slack"],
        ),
    )


def _env_snapshot() -> dict[str, str]:
    return resolve_environment_config().snapshot()


def _daemon_reachable(config_path: str | None) -> bool:
    status, _payload = _daemon_probe(config_path)
    return status == "ok"


def _daemon_probe(config_path: str | None) -> tuple[str, dict[str, Any]]:
    try:
        endpoint = resolve_daemon_endpoint(config_path)
        return probe_daemon_endpoint(endpoint)
    except Exception:
        return "unreachable", {}


def _timeout_seconds(args: argparse.Namespace) -> int:
    return max(0, int(getattr(args, "timeout_seconds", 30) or 30))


def _print_get_updates_conflict(action: str) -> None:
    print(
        f"A local OpenMinion daemon appears reachable. {action} uses "
        "getUpdates and may conflict with an active Telegram runner."
    )
    print(
        "Use known IDs with: "
        "openminion channel telegram pair --user-id ... --chat-id ..."
    )


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


def _path_parent_writable(raw_path: str) -> bool:
    path = Path(raw_path).expanduser()
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".openminion-write-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _count_active_pairings(sqlite_path: str) -> int:
    path = Path(sqlite_path).expanduser()
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM cp_pairings WHERE lower(status) = 'active'"
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] if row else 0)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    channel = subparsers.add_parser("channel", help="Channel setup and operations")
    channel_subcommands = channel.add_subparsers(dest="channel_name", required=True)
    telegram = channel_subcommands.add_parser(
        "telegram", help="Telegram channel setup, pairing, and status"
    )
    telegram_subcommands = telegram.add_subparsers(
        dest="telegram_command", required=True
    )

    setup = telegram_subcommands.add_parser("setup", help="Configure Telegram")
    _add_config_arg(setup)
    setup.add_argument("--bot-token-stdin", action="store_true")
    setup.add_argument("--bot-token-file", default=None)
    setup.add_argument("--bot-token-ref", default=None)
    setup.add_argument("--unsafe-bot-token", default=None)
    setup.add_argument("--allow-tracked-secret", action="store_true")
    setup.set_defaults(handler=run_channel, needs_app=False)

    doctor = telegram_subcommands.add_parser("doctor", help="Check Telegram setup")
    _add_config_arg(doctor)
    doctor.add_argument("--json", action="store_true")
    _add_telegram_scope_args(doctor)
    doctor.set_defaults(handler=run_channel, needs_app=False)

    identify = telegram_subcommands.add_parser(
        "identify", help="Discover Telegram user/chat IDs"
    )
    _add_config_arg(identify)
    identify.add_argument("--timeout-seconds", type=int, default=30)
    identify.set_defaults(handler=run_channel, needs_app=False)

    pair = telegram_subcommands.add_parser("pair", help="Create a pairing token")
    _add_config_arg(pair)
    pair.add_argument("--user-id", type=int, default=None)
    pair.add_argument("--chat-id", type=int, default=None)
    pair.add_argument("--ttl-seconds", type=int, default=None)
    pair.add_argument("--scopes", default=None)
    pair.add_argument("--wait", action="store_true")
    pair.add_argument("--timeout-seconds", type=int, default=30)
    pair.set_defaults(handler=run_channel, needs_app=False)

    run = telegram_subcommands.add_parser("run", help="Run the Telegram channel")
    _add_config_arg(run)
    run.add_argument("--once", action="store_true")
    run.set_defaults(handler=run_channel, needs_app=False)

    status = telegram_subcommands.add_parser("status", help="Show Telegram status")
    _add_config_arg(status)
    status.add_argument("--json", action="store_true")
    _add_telegram_scope_args(status)
    status.set_defaults(handler=run_channel, needs_app=False)

    commands_sync = telegram_subcommands.add_parser(
        "commands-sync", help="Sync Telegram slash-command menu"
    )
    _add_config_arg(commands_sync)
    commands_sync.set_defaults(handler=run_channel, needs_app=False)

    from openminion.modules.controlplane.channels.slack.cli import (
        register_slack_subcommands,
    )

    register_slack_subcommands(channel_subcommands, handler=run_channel)
    register_pairings_subcommands(channel_subcommands, handler=run_channel)


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Config file path")


def _add_telegram_scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--topic-id", type=int, default=None)
