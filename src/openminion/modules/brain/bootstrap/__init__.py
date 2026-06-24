from typing import Any

__all__ = ["skill_selection"]


def __getattr__(name: str) -> Any:
    if name == "skill_selection":
        from .skill import selection as skill_selection

        return skill_selection
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
