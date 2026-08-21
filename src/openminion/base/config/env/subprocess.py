"""Boundary helper for deterministic subprocess environment inheritance."""

import os
from collections.abc import Mapping
from typing import Final

from openminion.base.config.parse import split_comma_tokens


SUBPROCESS_ENV_ALLOWLIST_ENV: Final[str] = "OPENMINION_SUBPROCESS_ENV_ALLOW"
DEFAULT_SUBPROCESS_ENV_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "COLORTERM",
        "FORCE_COLOR",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_COLOR",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TZ",
        "USER",
    }
)

__all__: Final[tuple[str, ...]] = (
    "DEFAULT_SUBPROCESS_ENV_ALLOWLIST",
    "SUBPROCESS_ENV_ALLOWLIST_ENV",
    "build_subprocess_env",
)


def build_subprocess_env(
    overlay: Mapping[str, str] | None = None,
    *,
    inherit_parent: bool = True,
) -> dict[str, str]:
    env: dict[str, str] = {}
    if inherit_parent:
        parent = os.environ
        allowed = set(DEFAULT_SUBPROCESS_ENV_ALLOWLIST)
        raw = parent.get(SUBPROCESS_ENV_ALLOWLIST_ENV, "")
        allowed.update(split_comma_tokens(raw))
        env.update({key: parent[key] for key in sorted(allowed) if key in parent})
    if overlay:
        env.update({str(key): str(value) for key, value in overlay.items()})
    return env
