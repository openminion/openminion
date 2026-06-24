"""Plan tool package."""

import warnings

from openminion.tools.todo import TODO_FAMILY

from .registrar import REGISTRAR, register

warnings.warn(
    "`openminion.tools.plan` is deprecated; use `openminion.tools.todo` instead.",
    DeprecationWarning,
    stacklevel=2,
)

PLAN_FAMILY = TODO_FAMILY

__all__ = ["PLAN_FAMILY", "REGISTRAR", "register"]
