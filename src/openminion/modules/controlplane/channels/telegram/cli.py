import argparse
import os
from datetime import datetime, timezone
import threading
from pathlib import Path

from openminion.modules.controlplane.config import (
    load_config as load_controlplane_config,
)
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.config import (
    TelegramChannelConfig,
    load_config,
)
from openminion.modules.controlplane.channels.telegram.pairing import (
    PairCreateResult,
    TelegramPairingService,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)
from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
)
from openminion.modules.storage.module_cli import build_storage_argv, run_storage_argv
from openminion.base.logging import configure_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="openminion-controlplane-telegram")
    parser.add_argument(
        "action",
        nargs="?",
        choices=["run", "pair-create", "storage"],
        default="run",
        help="run telegram polling loop or create a secure pairing token",
    )
    parser.add_argument(
        "--config", type=str, default=None, help="Path to unified config YAML/JSON"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single polling cycle and exit"
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO", help="Python log level"
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Expected Telegram user id for pairing token",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="Expected chat id constraint for pairing token",
    )
    parser.add_argument(
        "--ttl-seconds", type=int, default=None, help="Pairing token TTL override"
    )
    parser.add_argument(
        "--scopes",
        type=str,
        default=None,
        help="Comma-separated scope list for least-privilege pairing grants",
    )
    parser.add_argument(
        "--token", type=str, default=None, help="Optional explicit token (tests only)"
    )
    add_common_module_root_args(parser)
    parser.add_argument(
        "--storage-command",
        default=None,
        help="Storage command (status/plan/migrate/backup/restore/verify/export/import)",
    )
    parser.add_argument("--db", default=None, help="SQLite database path override")
    parser.add_argument("--root", default=None, help="Blob root override")
    parser.add_argument(
        "--fallback", default=None, help="Fallback sidecar root override"
    )
    parser.add_argument("--snapshot-root", default=None)
    parser.add_argument("--snapshot-path", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--level", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--storage-input",
        dest="storage_input",
        default=None,
        help="OMX import input directory (storage import only)",
    )
    parser.add_argument("--skip-checksum", action="store_true")
    return parser.parse_args(argv)


def build_runtime(config_path: str | None):
    from openminion.cli.commands.channel import (
        _build_controlplane_components_from_base,
    )
    from openminion.cli.config import resolve_cli_roots

    base = _load_unified_compat_config(config_path)
    roots = resolve_cli_roots(config_path=config_path)
    components = _build_controlplane_components_from_base(
        base=base,
        home_root=roots.home_root,
        data_root=roots.data_root,
        logger_name="openminion.controlplane.telegram.compat",
    )
    return components.dispatcher


def build_runner(
    config_path: str | None,
) -> TelegramPollingRunner | TelegramWebhookRunner:
    registry, channel_id = build_channel_registry(config_path)
    return registry.get(channel_id)


def build_channel_registry(config_path: str | None):
    from openminion.cli.commands.channel import (
        _build_unified_telegram_runtime_from_base,
    )
    from openminion.cli.config import resolve_cli_roots

    base = _load_unified_compat_config(config_path)
    roots = resolve_cli_roots(config_path=config_path)
    try:
        foreground = _build_unified_telegram_runtime_from_base(
            base=base,
            home_root=roots.home_root,
            data_root=roots.data_root,
            logger_name="openminion.controlplane.telegram.compat",
        )
    except RuntimeError as exc:
        if "controlplane runtime components" in str(exc):
            raise SystemExit("channels.telegram.enabled is false") from exc
        raise
    return _UnifiedTelegramRegistry(foreground), "telegram"


class _UnifiedTelegramRegistry:
    def __init__(self, foreground) -> None:
        self._foreground = foreground

    def get(self, channel_id: str):
        if channel_id != "telegram":
            raise KeyError(channel_id)
        return self._foreground.runner

    def list(self) -> list[str]:
        return ["telegram"]

    def start_all(self, stop_event=None):
        self._foreground.start(stop_event=stop_event)
        return {"telegram": {"ok": True}}

    def stop_all(self):
        self._foreground.stop()
        return {"telegram": {"ok": True}}


def _load_unified_compat_config(config_path: str | None):
    from openminion.cli.config import load_cli_config

    base = load_cli_config(config_path)
    channels = dict(getattr(base, "channels", {}) or {})
    cp_cfg = load_controlplane_config(config_path, env=dict(os.environ))
    channels.setdefault(
        "controlplane",
        {
            "sqlite_path": cp_cfg.sqlite_path,
            "wal": cp_cfg.wal,
            "openminion_enabled": cp_cfg.openminion_enabled,
            "openminion_config_path": cp_cfg.openminion_config_path,
            "openminion_channel": cp_cfg.openminion_channel,
            "openminion_target": cp_cfg.openminion_target,
            "openminion_deliver": cp_cfg.openminion_deliver,
        },
    )
    tg_cfg = load_config(config_path, env=dict(os.environ)).telegram
    if tg_cfg.enabled:
        enabled = list(getattr(base, "enabled_channels", []) or [])
        if "telegram" not in enabled:
            enabled.append("telegram")
        base.enabled_channels = enabled
    base.channels = channels
    return base


def create_pair_token(args: argparse.Namespace) -> None:
    scopes = _parse_scopes(args.scopes)
    try:
        cfg, issued = issue_pair_token_for_cli(
            config_path=args.config,
            user_id=args.user_id,
            chat_id=args.chat_id,
            ttl_seconds=args.ttl_seconds,
            scopes=scopes,
            token=args.token,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    expires_iso = datetime.fromtimestamp(
        issued.expires_at_ts, tz=timezone.utc
    ).isoformat()
    print(f"PAIR_TOKEN={issued.token}")
    print(f"PAIR_TOKEN_HINT={issued.token_hint}")
    print(f"PAIR_TOKEN_HASH_PREFIX={issued.token_hash_prefix}")
    print(f"PAIR_EXPIRES_AT={expires_iso}")
    print(f"PAIR_SCOPES={','.join(issued.scopes)}")

    if cfg.bot_token:
        try:
            me = TelegramBotAPI(cfg.bot_token).get_me()
            username = str(me.get("username") or "").strip()
            if username:
                print(f"PAIR_DEEP_LINK=https://t.me/{username}?start={issued.token}")
        except Exception:
            pass


def issue_pair_token_for_cli(
    *,
    config_path: str | None,
    user_id: int | str | None,
    chat_id: int | str | None,
    ttl_seconds: int | None = None,
    scopes: list[str] | None = None,
    token: str | None = None,
) -> tuple[TelegramChannelConfig, PairCreateResult]:
    cfg = load_config(config_path, env=dict(os.environ)).telegram
    if not cfg.enabled:
        raise RuntimeError("channels.telegram.enabled is false")
    if user_id is None and chat_id is None:
        raise RuntimeError("pair-create requires --user-id and/or --chat-id")

    store = TelegramPollStateStore(cfg.polling.state_sqlite_path)
    cp_cfg = load_controlplane_config(config_path, env=dict(os.environ))
    cp_store = SQLiteControlPlaneStore(cp_cfg.sqlite_path, wal=cp_cfg.wal)
    try:
        pairing = TelegramPairingService(
            config=cfg.pairing,
            store=store,
            controlplane_store=cp_store,
        )
        issued = pairing.issue_token(
            expected_user_id=int(user_id) if user_id is not None else None,
            expected_chat_id=int(chat_id) if chat_id is not None else None,
            token_ttl_seconds=ttl_seconds or cfg.pairing.token_ttl_seconds,
            scopes=scopes or list(cfg.pairing.default_scopes),
            token=token,
        )
        return cfg, issued
    finally:
        cp_store.close()
        store.close()


def _parse_scopes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [scope for item in raw.split(",") if (scope := item.strip())]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(str(args.log_level or "INFO"))
    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    if args.action == "pair-create":
        create_pair_token(args)
        return 0
    if args.action == "storage":
        cfg = load_config(args.config, env=dict(os.environ)).telegram
        db_path = (
            Path(str(args.db)).expanduser().resolve(strict=False)
            if args.db
            else Path(cfg.polling.state_sqlite_path).expanduser().resolve(strict=False)
        )
        if not args.storage_command:
            raise SystemExit("storage action requires --storage-command")
        argv = build_storage_argv(
            module_id="controlplane_telegram",
            db_path=db_path,
            command=str(args.storage_command),
            home_root=home_root or None,
            data_root=data_root or None,
            root=str(getattr(args, "root", "") or "").strip() or None,
            fallback=str(getattr(args, "fallback", "") or "").strip() or None,
            snapshot_root=str(getattr(args, "snapshot_root", "") or "").strip() or None,
            snapshot_path=str(getattr(args, "snapshot_path", "") or "").strip() or None,
            mode=str(getattr(args, "mode", "") or "").strip() or None,
            level=str(getattr(args, "level", "") or "").strip() or None,
            out=str(getattr(args, "out", "") or "").strip() or None,
            notes=str(getattr(args, "notes", "") or "").strip() or None,
            input_dir=str(getattr(args, "storage_input", "") or "").strip() or None,
            skip_checksum=bool(args.skip_checksum),
        )
        run_storage_argv(argv)
        return 0

    registry, channel_id = build_channel_registry(args.config)
    runner = registry.get(channel_id)

    if args.once:
        try:
            run_once = getattr(runner, "run_once", None)
            if not callable(run_once):
                raise SystemExit("--once is only supported in polling mode")
            run_once()
        finally:
            registry.stop_all()
        return 0

    stop = threading.Event()
    try:
        registry.start_all(stop_event=stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        registry.stop_all()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
