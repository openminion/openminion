from dataclasses import dataclass

from .constants import OPENMINION_SECRET_KEY_ENV


@dataclass(frozen=True)
class SecretConfig:
    master_key_env: str = OPENMINION_SECRET_KEY_ENV


def load_config(*_args: object, **_kwargs: object) -> SecretConfig:
    return SecretConfig()
