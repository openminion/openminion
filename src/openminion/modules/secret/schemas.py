from dataclasses import dataclass


@dataclass
class SecretRecord:
    """A stored secret record."""

    key: str
    namespace: str
    created_at: float
    updated_at: float


@dataclass
class SecretNamespace:
    """A namespace for grouping secrets."""

    name: str
    secret_count: int
    created_at: float


class SecretError(Exception):
    """Base exception for secret operations."""

    pass


class SecretKeyError(SecretError):
    """Raised when secret key is missing or invalid."""

    pass


class SecretNotFoundError(SecretError):
    """Raised when a secret is not found."""

    pass


class SecretEncryptionError(SecretError):
    """Raised when encryption/decryption fails."""

    pass
