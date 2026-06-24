from dataclasses import dataclass

from .constants import DEFAULT_IP_PROVIDER_ID

DEFAULT_IP_PUBLIC_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class IpToolConfig:
    default_provider_id: str = DEFAULT_IP_PROVIDER_ID
    timeout_seconds: float = DEFAULT_IP_PUBLIC_TIMEOUT_SECONDS


def load_config(*_args: object, **_kwargs: object) -> IpToolConfig:
    return IpToolConfig()


__all__ = ["DEFAULT_IP_PUBLIC_TIMEOUT_SECONDS", "IpToolConfig", "load_config"]
