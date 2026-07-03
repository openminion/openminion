"""Load OpenMinion validation policy data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "scripts" / "policy" / "openminion" / "quality_policy.json"
)


def load_quality_policy(path: Path | None = None) -> dict[str, Any]:
    """Return the OpenMinion quality policy config."""

    policy_path = path or DEFAULT_POLICY_PATH
    try:
        with policy_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise SystemExit(f"cannot read quality policy {policy_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"quality policy must be a JSON object: {policy_path}")
    return payload
