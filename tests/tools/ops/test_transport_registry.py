import pytest

from openminion.tools.ops.contracts import LocalTarget
from openminion.tools.ops.transports import (
    BUILTIN_TRANSPORTS,
    TransportRegistration,
    build_transports,
    registration_map,
)


def test_builtin_registration_is_explicit_and_complete() -> None:
    registrations = registration_map()

    assert tuple(registrations) == (
        "local",
        "container",
        "ssh",
        "winrm",
        "kubernetes",
        "ssm",
    )
    assert registrations["local"].target_schema is LocalTarget
    assert registrations["local"].capabilities == {"command", "file_read"}
    assert set(build_transports(("local",), credential_reader=lambda _ref: "")) == {
        "local"
    }


def test_duplicate_registration_fails_deterministically() -> None:
    duplicate = TransportRegistration(
        kind="local",
        target_schema=LocalTarget,
        capabilities=frozenset({"command"}),
        factory=lambda _reader: object(),
    )

    with pytest.raises(ValueError, match="duplicate transport registration"):
        registration_map((*BUILTIN_TRANSPORTS, duplicate))
