from typing import Any


def runner_from_context(ctx: Any) -> Any | None:
    return getattr(getattr(ctx, "_services", None), "runner", None)
