from dataclasses import dataclass
from typing import Protocol


SECRET_INTERFACE_VERSION = "v1"


def ensure_secret_interface_compatibility(actual_version: str) -> bool:
    """Validate that actual interface version is compatible with expected version."""
    if actual_version == SECRET_INTERFACE_VERSION:
        return True
    raise ValueError(
        f"Secret interface version mismatch: expected {SECRET_INTERFACE_VERSION}, got {actual_version}"
    )


@dataclass
class SecretContractConfig:
    """Configuration for secret service contract."""

    db_path: str | None = None
    master_key: str | None = None


class SecretContract(Protocol):
    """Protocol defining the secret interface contract."""

    def __init__(
        self, db_path: str | None = ..., master_key: str | None = ...
    ) -> None: ...

    async def close(self) -> None: ...

    async def set_secret(
        self, key: str, value: str, *, namespace: str = ...
    ) -> None: ...

    async def get_secret(self, key: str, *, namespace: str = ...) -> str: ...

    async def delete_secret(self, key: str, *, namespace: str = ...) -> None: ...

    async def list_keys(self, namespace: str = ...) -> list[str]: ...
