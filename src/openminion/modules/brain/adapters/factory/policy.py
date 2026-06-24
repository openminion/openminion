from pathlib import Path
from typing import Any

from openminion.modules.brain.adapters.policy import (
    LocalPolicyAdapter,
    create_policy_runtime_adapter,
)

from .environment import default_data_root
from .modes import mode_is_local, raise_if_strict


def create_policy_adapter(
    mode: str = "auto",
    db_path: str | Path | None = None,
    *,
    policy_service: Any | None = None,
    action_policy_config: Any | None = None,
) -> Any:
    if mode_is_local(mode):
        return LocalPolicyAdapter()
    try:
        resolved_path = (
            Path(db_path) if db_path else default_data_root() / "policy" / "policy.db"
        )
        return create_policy_runtime_adapter(
            db_path=resolved_path,
            policy_service=policy_service,
            action_policy_config=action_policy_config,
        )
    except ImportError:
        raise_if_strict(mode)
        return LocalPolicyAdapter()
