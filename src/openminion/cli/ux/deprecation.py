from __future__ import annotations

import sys

_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def deprecation_suppressed(env_name: str) -> bool:
    """Read deprecation suppression through the centralized env owner."""
    from openminion.base.config.env import EnvironmentConfig

    value = str(EnvironmentConfig.from_sources().get(env_name, "") or "")
    return value.strip().lower() in _TRUTHY_VALUES


def print_deprecation_notice(text: str, *, suppression_env: str) -> bool:
    if deprecation_suppressed(suppression_env):
        return False
    print(text, file=sys.stderr)
    return True
