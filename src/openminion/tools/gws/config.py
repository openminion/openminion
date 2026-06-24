from .schemas import GwsToolConfig


def load_config(payload: object | None = None) -> GwsToolConfig:
    return GwsToolConfig.model_validate(payload or {})


__all__ = ["GwsToolConfig", "load_config"]
