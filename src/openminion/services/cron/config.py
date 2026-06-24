from dataclasses import dataclass


@dataclass(frozen=True)
class CronConfig:
    pass


def load_config(*_args: object, **_kwargs: object) -> CronConfig:
    return CronConfig()
