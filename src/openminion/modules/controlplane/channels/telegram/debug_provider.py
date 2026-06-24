import importlib

from openminion.base.debug import (
    DebugProvider,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
)


class TelegramDebugProvider(DebugProvider):
    """TGIT-06: Debug provider for Telegram adapter module.

    Provides debug payload for `/debug --module=openminion-controlplane-telegram`.
    """

    def __init__(self) -> None:
        super().__init__(
            module_name="openminion-controlplane-telegram",
            probe_fn=self._probe,
            wiring_check_fn=None,
        )

    def _probe(self) -> ModuleDebugPayload:
        """Probe the Telegram module status."""
        try:
            from openminion.modules.controlplane.channels.telegram.config import (
                load_config,
            )

            home_root = None
            try:
                from openminion.base.config import bootstrap_home_paths

                home_root = bootstrap_home_paths().home_root
            except Exception:
                home_root = None

            cfg = load_config(home_root=home_root).telegram
            details = {
                "mode": cfg.mode,
                "enabled": cfg.enabled,
                "webhook_supported": True,
                "polling_supported": True,
                "state_sqlite_path": cfg.polling.state_sqlite_path,
                "path_mode": cfg.polling.path_mode,
                "path_source": cfg.polling.path_source,
                "home_root": cfg.polling.home_root,
            }

            try:
                importlib.import_module(
                    "openminion.modules.controlplane.channels.telegram.webhook"
                )
                details["webhook_available"] = True
            except ImportError:
                details["webhook_available"] = False

            return ModuleDebugPayload(
                module="openminion-controlplane-telegram",
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                details=details,
                resolved_path=cfg.polling.state_sqlite_path,
                path_mode=cfg.polling.path_mode,
                path_source=cfg.polling.path_source,
            )
        except Exception as exc:
            return ModuleDebugPayload(
                module="openminion-controlplane-telegram",
                status=DebugStatus.WARN,
                mode="runtime",
                wiring_source=WiringSource.STUB,
                last_error=str(exc),
                details={"note": "Could not fully probe telegram module"},
            )
