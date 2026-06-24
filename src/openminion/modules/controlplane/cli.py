import os
from pathlib import Path

from openminion.modules.controlplane.adapters.cli import (
    CLIAdapter,
    parse_cli_args,
)
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.config import ControlPlaneConfig, load_config
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.adapters.client import (
    OpenMinionBrainClient,
    OpenMinionIntegrationError,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from openminion.modules.cli_common import apply_home_data_root_env
from openminion.modules.storage.module_cli import build_storage_argv, run_storage_argv


def main(argv: list[str] | None = None) -> int:
    args = parse_cli_args(argv)
    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)
    cfg = load_config(args.config, env=dict(os.environ))
    if getattr(args, "storage_command", None):
        db_path = Path(cfg.sqlite_path).expanduser().resolve(strict=False)
        argv = build_storage_argv(
            module_id="controlplane",
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
            skip_checksum=bool(getattr(args, "skip_checksum", False)),
        )
        run_storage_argv(argv)
        return 0
    store = _build_store(cfg)
    router = Router(store)
    parser = SlashCommandParser()
    auth = AuthEvaluator(admin_user_keys=cfg.admin_user_keys)
    registry = CommandRegistry(store, auth=auth)
    brain = _build_brain(cfg)

    def outbound(payload: dict) -> None:
        print(payload.get("text", str(payload)))

    runtime = ControlPlaneDispatcher(
        store=store,
        router=router,
        parser=parser,
        command_registry=registry,
        brain_client=brain,
        outbound_sender=outbound,
    )

    adapter = CLIAdapter(handler=runtime, once=args.once, input_text=args.input)
    try:
        adapter.start()
    finally:
        _safe_close(brain)
        _safe_close(store)
    return 0


def _build_store(
    cfg: ControlPlaneConfig,
) -> InMemoryControlPlaneStore | SQLiteControlPlaneStore:
    if cfg.store_backend.lower() == "sqlite":
        return SQLiteControlPlaneStore(cfg.sqlite_path, wal=cfg.wal)
    return InMemoryControlPlaneStore()


def _build_brain(cfg: ControlPlaneConfig):
    if not cfg.openminion_enabled:
        return EchoBrain()
    try:
        return OpenMinionBrainClient(
            config_path=cfg.openminion_config_path,
            channel=cfg.openminion_channel,
            target=cfg.openminion_target,
            deliver=cfg.openminion_deliver,
        )
    except OpenMinionIntegrationError as exc:  # pragma: no cover - configuration issue
        raise SystemExit(str(exc)) from exc


def _safe_close(resource: object) -> None:
    closer = getattr(resource, "close", None)
    if callable(closer):
        closer()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
