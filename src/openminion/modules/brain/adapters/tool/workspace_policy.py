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


def add_workspace_roots(policy: Policy, roots: tuple[Path, ...]) -> Policy:
    policy_raw = copy.deepcopy(policy.raw)
    paths = policy_raw.setdefault("paths", {})
    if not isinstance(paths, dict):
        return policy
    root_values = [str(root) for root in roots]
    for key in ("read_allow", "write_allow"):
        paths[key] = [*list(paths.get(key, []) or []), *root_values]
    return Policy(raw=policy_raw)


def workspace_context_policy(
    policy: Policy,
    *,
    args: dict[str, Any],
    parent: Path,
    requested: str,
    active: Path | None,
    added_roots: tuple[Path, ...],
) -> Policy:
    if requested and (
        active is None
        or Path(requested).expanduser().resolve(strict=False)
        != active.resolve(strict=False)
    ):
        raise ValueError("The requested child workspace is not active.")
    if active is not None:
        policy = child_workspace_policy(policy, args=args, parent=parent, child=active)
    if any(
        not root.is_dir() or root.resolve(strict=False) != root for root in added_roots
    ):
        raise ValueError("An added workspace directory is no longer valid.")
    return add_workspace_roots(policy, added_roots) if added_roots else policy
