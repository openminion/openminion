from __future__ import annotations

import getpass


def local_operator_id() -> str:
    """Return the local OS identity used by operator-only CLI actions."""

    return f"local:{getpass.getuser()}"


__all__ = ["local_operator_id"]
