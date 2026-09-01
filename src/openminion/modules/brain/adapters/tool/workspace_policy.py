from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from openminion.modules.tool import Policy


def rebase_child_path_argument(
    args: dict[str, Any], *, parent: Path, child: Path
) -> None:
    raw_path = str(args.get("path", "") or "").strip()
    candidate = Path(raw_path).expanduser()
    if not raw_path or not candidate.is_absolute():
        return
    try:
        relative_path = candidate.resolve(strict=False).relative_to(
            parent.resolve(strict=False)
        )
    except ValueError:
        return
    args["path"] = str(child / relative_path)


def child_workspace_policy(
    policy: Policy,
    *,
    args: dict[str, Any],
    parent: Path,
    child: Path,
) -> Policy:
    rebase_child_path_argument(args, parent=parent, child=child)
    policy_raw = copy.deepcopy(policy.raw)
    policy_raw["workspace_root"] = str(child)
    context_metadata = policy_raw.setdefault("context_metadata", {})
    if isinstance(context_metadata, dict):
        context_metadata["workspace_root"] = str(child)
        context_metadata["cwd"] = str(child)
    return Policy(raw=policy_raw)
