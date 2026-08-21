from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..contracts import (
    ContainerTarget,
    KubernetesTarget,
    LocalTarget,
    OperationTarget,
    SshTarget,
    SsmTarget,
    TargetKind,
    TransportCapability,
    WinrmTarget,
)
from ..interfaces import TargetTransport
from .container import ContainerTransport
from .kubernetes import KubernetesTransport
from .local import LocalTransport
from .ssh import CredentialReader, SshTransport
from .ssm import SsmTransport
from .winrm import WinrmTransport


@dataclass(frozen=True)
class TransportRegistration:
    kind: TargetKind
    target_schema: type[OperationTarget]
    capabilities: frozenset[TransportCapability]
    factory: Callable[[CredentialReader], TargetTransport]


BUILTIN_TRANSPORTS = (
    TransportRegistration(
        "local",
        LocalTarget,
        frozenset({"command", "file_read"}),
        lambda _reader: LocalTransport(),
    ),
    TransportRegistration(
        "container",
        ContainerTarget,
        frozenset({"command", "file_read"}),
        lambda _reader: ContainerTransport(),
    ),
    TransportRegistration(
        "ssh", SshTarget, frozenset({"command", "file_read"}), SshTransport
    ),
    TransportRegistration("winrm", WinrmTarget, frozenset({"command"}), WinrmTransport),
    TransportRegistration(
        "kubernetes", KubernetesTarget, frozenset({"command"}), KubernetesTransport
    ),
    TransportRegistration("ssm", SsmTarget, frozenset({"command"}), SsmTransport),
)


def registration_map(
    registrations: Iterable[TransportRegistration] = BUILTIN_TRANSPORTS,
) -> dict[TargetKind, TransportRegistration]:
    result: dict[TargetKind, TransportRegistration] = {}
    for registration in registrations:
        if registration.kind in result:
            raise ValueError(f"duplicate transport registration: {registration.kind}")
        result[registration.kind] = registration
    return result


def build_transports(
    kinds: Iterable[TargetKind],
    *,
    credential_reader: CredentialReader,
) -> dict[str, TargetTransport]:
    registrations = registration_map()
    result: dict[str, TargetTransport] = {}
    for kind in kinds:
        try:
            factory = registrations[kind].factory
        except KeyError as exc:
            raise ValueError(f"unknown transport registration: {kind}") from exc
        result[kind] = factory(credential_reader)
    return result
