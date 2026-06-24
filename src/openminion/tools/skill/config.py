from dataclasses import dataclass

SKILL_URL_FETCH_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class SkillToolConfig:
    enabled: bool = True


def load_config(*_args: object, **_kwargs: object) -> SkillToolConfig:
    return SkillToolConfig()


__all__ = ["SkillToolConfig", "load_config"]
