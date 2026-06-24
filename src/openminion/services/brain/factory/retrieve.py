from pathlib import Path
from typing import Any

from openminion.modules.brain.adapters.factory import create_retrieve_adapter


def build_retrieve_service(
    *,
    home_root: Path,
    vector_adapter: Any | None,
    config: Any | None,
    logger: Any,
    telemetryctl: Any | None = None,
) -> Any | None:
    try:
        from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "retrievectl unavailable; continuing without retrieve service: %s",
            exc,
        )
        return None

    try:
        retrieve_cfg = config
        if retrieve_cfg is None:
            from openminion.modules.retrieve import load_config as load_retrieve_config

            retrieve_cfg = load_retrieve_config(home_root=home_root)

        return RetrieveCtl(
            config={
                "version": retrieve_cfg.version,
                "retrievectl": {
                    "storage": {
                        "sqlite_path": str(retrieve_cfg.storage.sqlite_path),
                        "blob_root": str(retrieve_cfg.storage.blob_root),
                        "wal_mode": retrieve_cfg.storage.wal_mode,
                    },
                    "defaults": retrieve_cfg.defaults.model_dump(),
                },
            },
            vector_adapter=vector_adapter,
            telemetryctl=telemetryctl,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "retrievectl unavailable; continuing without retrieve service: %s",
            exc,
        )
        return None


def init_retrieve_adapter(
    *,
    mode: str,
    home_root: Path,
    vector_adapter: Any | None,
    config: Any | None,
    logger: Any,
    retrieve_service: Any | None = None,
    telemetryctl: Any | None = None,
) -> Any | None:
    service = retrieve_service
    if service is None:
        service = build_retrieve_service(
            home_root=home_root,
            vector_adapter=vector_adapter,
            config=config,
            logger=logger,
            telemetryctl=telemetryctl,
        )
    if service is None:
        return None

    try:
        storage_cfg = getattr(getattr(service, "_config", object()), "storage", None)
        if storage_cfg is not None:
            logger.info(
                "Retrieve adapter initialized: sqlite_path=%s blob_root=%s path_mode=%s path_source=%s",
                getattr(storage_cfg, "sqlite_path", ""),
                getattr(storage_cfg, "blob_root", ""),
                getattr(storage_cfg, "path_mode", ""),
                getattr(storage_cfg, "path_source", ""),
            )
        else:
            logger.info("Retrieve adapter initialized with pre-built retrieve service")
        return create_retrieve_adapter(
            mode=mode,
            service=service,
            telemetryctl=telemetryctl,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "retrievectl unavailable; continuing without retrieve adapter: %s",
            exc,
        )
        return None
