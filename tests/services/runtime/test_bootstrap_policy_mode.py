from __future__ import annotations

from openminion.services.runtime.bootstrap import _map_action_policy_mode
from openminion.services.runtime.constants import (
    ACTION_POLICY_MODE_DEFAULT_ALIAS,
    ACTION_POLICY_MODE_DISABLED,
    ACTION_POLICY_MODE_ENFORCE,
    ACTION_POLICY_MODE_ENFORCE_SAFE,
)


def test_map_action_policy_mode_uses_canonical_runtime_constants() -> None:
    assert _map_action_policy_mode("bypass") == ACTION_POLICY_MODE_DISABLED
    assert _map_action_policy_mode("ask") == ACTION_POLICY_MODE_ENFORCE
    assert (
        _map_action_policy_mode(ACTION_POLICY_MODE_DEFAULT_ALIAS)
        == ACTION_POLICY_MODE_ENFORCE_SAFE
    )
    assert _map_action_policy_mode("unknown") == ACTION_POLICY_MODE_ENFORCE
