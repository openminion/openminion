from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.services.diagnostics.owner_status import build_owner_status


@dataclass
class OwnerStatusQueryError(RuntimeError):
    message: str
    code: str = "invalid_request"

    def __str__(self) -> str:
        return self.message


def get_owner_status(
    config_path: str | None,
    *,
    session_limit: int = 20,
    run_limit_per_session: int = 20,
    window_hours: int = 24,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    if int(session_limit) <= 0:
        raise OwnerStatusQueryError("`session_limit` must be greater than zero.")
    if int(run_limit_per_session) <= 0:
        raise OwnerStatusQueryError("`run_limit` must be greater than zero.")
    if int(window_hours) <= 0:
        raise OwnerStatusQueryError("`hours` must be greater than zero.")

    own_runtime = runtime is None
    active_runtime = runtime or APIRuntime.from_config_path(config_path)
    try:
        return build_owner_status(
            config_path=config_path,
            runtime=active_runtime,
            session_limit=int(session_limit),
            run_limit_per_session=int(run_limit_per_session),
            window_hours=int(window_hours),
        )
    finally:
        if own_runtime:
            active_runtime.close()
