from dataclasses import dataclass

from .constants import POLICY_MODE_DISABLED


@dataclass(frozen=True)
class PolicyModuleConfig:
    mode: str = POLICY_MODE_DISABLED


def load_config(*_args: object, **_kwargs: object) -> PolicyModuleConfig:
    return PolicyModuleConfig()
