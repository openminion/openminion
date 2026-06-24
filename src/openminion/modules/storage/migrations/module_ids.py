from collections.abc import Iterable

from openminion.modules.storage.constants import DEFAULT_SCHEMA_HEAD

MODULE_APPLICATION_IDS: dict[str, int] = {
    "storage": 0x4F4D0000,
    "session": 0x4F4D0001,
    "artifact": 0x4F4D0002,
    "registry": 0x4F4D0003,
    "identity": 0x4F4D0004,
    "policy": 0x4F4D0005,
    "secret": 0x4F4D0006,
    "telemetry": 0x4F4D0007,
    "controlplane": 0x4F4D0008,
    "controlplane_telegram": 0x4F4D0009,
    "retrieve": 0x4F4D000A,
    "memory": 0x4F4D000B,
    "skill": 0x4F4D000C,
    "a2a": 0x4F4D000D,
    "compress": 0x4F4D000E,
    "task": 0x4F4D000F,
    "authoring": 0x4F4D0010,
    "brain": 0x4F4D0011,
}


def get_module_application_id(module_id: str) -> int:
    normalized = str(module_id).strip().lower()
    if not normalized:
        raise ValueError("module_id is required to resolve module application id")
    if normalized not in MODULE_APPLICATION_IDS:
        known = ", ".join(sorted(MODULE_APPLICATION_IDS))
        raise ValueError(f"Unknown module_id={module_id!r}; known: {known}")
    return MODULE_APPLICATION_IDS[normalized]


def module_id_from_package(package: str | None) -> str:
    if not package:
        raise ValueError("package is required to infer module id")
    parts = package.split(".")
    if (
        len(parts) >= 4
        and parts[-1] == "storage"
        and parts[-4:-2] == ["controlplane", "channels"]
    ):
        return f"controlplane_{parts[-2]}"
    if len(parts) >= 3 and parts[-3:-1] == ["controlplane", "channels"]:
        return f"controlplane_{parts[-1]}"
    if len(parts) >= 2 and parts[-1] == "storage":
        return parts[-2]
    return parts[-1]


def schema_head_from_migrations(
    migrations: Iterable[str] | None,
    *,
    fallback: str = DEFAULT_SCHEMA_HEAD,
) -> str:
    if migrations is None:
        return fallback
    items = list(migrations)
    if not items:
        return fallback
    return str(items[-1])
