from dataclasses import dataclass

from .constants import UTILITY_MAX_AST_NODES


@dataclass(frozen=True)
class UtilityToolConfig:
    max_ast_nodes: int = UTILITY_MAX_AST_NODES


def load_config(*_args: object, **_kwargs: object) -> UtilityToolConfig:
    return UtilityToolConfig()


__all__ = ["UtilityToolConfig", "load_config"]
