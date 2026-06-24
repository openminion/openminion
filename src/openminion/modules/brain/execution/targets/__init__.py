# ruff: noqa: F401

from .delegated import build_delegated_decision, is_delegated_target


def is_local_target(target: object) -> bool:
    kind = str(getattr(target, "kind", "") or "").strip().lower()
    return kind in {"", "local"}
