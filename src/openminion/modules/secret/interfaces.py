from dataclasses import dataclass
from typing import Mapping, Protocol


SECRET_INTERFACE_VERSION = "v1"
SECRET_KEY_RING_INTERFACE_VERSION = "secret_key_ring.v1"


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


@dataclass(frozen=True)
class SecretKeyDescriptor:
    key_id: str
    purpose: str
    active: bool


class SecretKeyRingContract(Protocol):
    """Purpose-bound key-ring contract consumed by session encryption adapters."""

    @property
    def contract_version(self) -> str: ...

    @property
    def active_key_id(self) -> str: ...

    def encrypt_for_purpose(
        self,
        *,
        plaintext: bytes,
        purpose: str,
        record_identity: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def decrypt_for_purpose(
        self,
        envelope: Mapping[str, object],
        *,
        expected_purpose: str,
        expected_record_identity: Mapping[str, str],
    ) -> bytes: ...

    def rotate_active_key(self, *, key_id: str) -> SecretKeyDescriptor: ...

    def can_remove_key(self, key_id: str, *, referenced_key_ids: set[str]) -> bool: ...
