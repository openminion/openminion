from dataclasses import dataclass


@dataclass(frozen=True)
class SessionConfig:
    pass


def load_config(*_args: object, **_kwargs: object) -> SessionConfig:
    return SessionConfig()
