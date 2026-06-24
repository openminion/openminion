from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest
import yaml

from openminion.modules.tool.runtime.policy import DEFAULT_POLICY


@pytest.fixture
def workspace_fixture(tmp_path: Path) -> Tuple[Path, Path]:

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    policy_dst = tmp_path / ".tmp" / "configs" / "policy.yaml"
    policy_dst.parent.mkdir(parents=True, exist_ok=True)
    policy_dst.write_text(
        yaml.safe_dump(DEFAULT_POLICY, sort_keys=False),
        encoding="utf-8",
    )

    return workspace_dir, policy_dst
