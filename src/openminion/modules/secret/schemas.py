class SecretError(Exception):
    """Base exception for secret operations."""


class SecretKeyError(SecretError):
    """Raised when secret key is missing or invalid."""


class SecretNotFoundError(SecretError):
    """Raised when a secret is not found."""


class SecretEncryptionError(SecretError):
    """Raised when encryption/decryption fails."""
