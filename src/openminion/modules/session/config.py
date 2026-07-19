from dataclasses import dataclass


@dataclass(frozen=True)
class SessionConfig:
    turn_lease_enabled: bool = True
    turn_lease_ttl_s: int = 60
    turn_lease_wait_timeout_s: float = 0.0


def load_config(*_args: object, **_kwargs: object) -> SessionConfig:
    return SessionConfig()
