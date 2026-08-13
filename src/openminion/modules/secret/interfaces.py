SECRET_INTERFACE_VERSION = "v1"
SECRET_KEY_RING_INTERFACE_VERSION = "secret_key_ring.v1"


def ensure_secret_interface_compatibility(actual_version: str) -> bool:
    """Validate that actual interface version is compatible with expected version."""
    if actual_version == SECRET_INTERFACE_VERSION:
        return True
    raise ValueError(
        f"Secret interface version mismatch: expected {SECRET_INTERFACE_VERSION}, got {actual_version}"
    )
