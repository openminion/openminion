"""Boundary helper for deterministic subprocess environment inheritance."""

import os
from collections.abc import Iterable
from collections.abc import Mapping

from openminion.base.config.parse import split_comma_tokens


SUBPROCESS_ENV_ALLOWLIST_ENV = "OPENMINION_SUBPROCESS_ENV_ALLOW"
DEFAULT_SUBPROCESS_ENV_ALLOWLIST = frozenset(
    "COLORTERM FORCE_COLOR HOME LANG LC_ALL LC_CTYPE NO_COLOR PATH SHELL TERM TMPDIR TZ USER".split()
)


def _inherited_env(keys: bool | Iterable[str]) -> dict[str, str]:
    parent = os.environ
    allowed = (
        set(DEFAULT_SUBPROCESS_ENV_ALLOWLIST)
        if isinstance(keys, bool)
        else {str(key) for key in keys}
    )
    if keys is True:
        allowed.update(split_comma_tokens(parent.get(SUBPROCESS_ENV_ALLOWLIST_ENV, "")))
    return {key: parent[key] for key in sorted(allowed) if key in parent}


def build_subprocess_env(
    overlay: Mapping[str, str] | None = None,
    *,
    inherit_parent: bool | Iterable[str] = True,
) -> dict[str, str]:
    env = _inherited_env(inherit_parent) if inherit_parent else {}
    if overlay:
        env.update({str(key): str(value) for key, value in overlay.items()})
    return env
