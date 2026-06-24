from dataclasses import dataclass


@dataclass(frozen=True)
class ReactionToolConfig:
    enabled: bool = True


def load_config(*_args: object, **_kwargs: object) -> ReactionToolConfig:
    return ReactionToolConfig()


__all__ = ["ReactionToolConfig", "load_config"]
