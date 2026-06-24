from __future__ import annotations

from pathlib import Path

from openminion.base.generated_paths import resolve_generated_config_path

from .config import RetrieveCtlConfig, load_config
from .constants import DEFAULT_CONFIG_FILENAME
from .interfaces import (
    RETRIEVE_INTERFACE_VERSION,
    RetrieveCtlInterface,
    ensure_retrieve_compatibility,
)
from .runtime.retrieve import RetrieveCtl
from .schemas import (
    DocUnit,
    GroupLongUnitsResult,
    IngestResult,
    RaptorBuildResult,
    RetrievalFilters,
    RetrievedItem,
    RetrieveRequest,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent.parent


def resolve_config_path(filename: str | None = None) -> Path:
    if filename is None:
        candidate = PROJECT_ROOT / DEFAULT_CONFIG_FILENAME
        if candidate.exists():
            return candidate
        generated = Path(resolve_generated_config_path(DEFAULT_CONFIG_FILENAME))
        if not generated.exists():
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_text("{}\n", encoding="utf-8")
        return generated

    path = Path(filename)
    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    if not candidate.exists():
        raise FileNotFoundError(f"config file not found: {candidate}")
    return candidate


__all__ = (
    "RetrieveCtl",
    "RetrieveCtlConfig",
    "RetrieveCtlInterface",
    "RETRIEVE_INTERFACE_VERSION",
    "ensure_retrieve_compatibility",
    "load_config",
    "resolve_config_path",
    "RetrieveRequest",
    "RetrievalFilters",
    "RetrievedItem",
    "DocUnit",
    "IngestResult",
    "RaptorBuildResult",
    "GroupLongUnitsResult",
)
