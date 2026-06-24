from pathlib import Path
from typing import Iterable

from .protocol import ApprovalCriteria


_CRITERIA_DIR = Path(__file__).parent / "criteria"


class ApprovalCriteriaRegistry:
    def __init__(self) -> None:
        self._criteria: dict[tuple[str, str], ApprovalCriteria] = {}

    def register(self, criteria: ApprovalCriteria) -> None:
        self._criteria[(criteria.tool_id, criteria.action)] = criteria

    def get(self, tool_id: str, action: str) -> ApprovalCriteria | None:
        return self._criteria.get((tool_id, action))

    def keys(self) -> Iterable[tuple[str, str]]:
        return self._criteria.keys()


def _load_markdown_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def default_criteria_registry() -> ApprovalCriteriaRegistry:
    registry = ApprovalCriteriaRegistry()
    for tool_id, action, filename in (
        ("git", "reset_hard", "git_reset_hard.md"),
        ("git", "branch_force_delete", "git_branch_force_delete.md"),
        ("git", "stash_drop", "git_stash_drop.md"),
        ("git", "stash_clear", "git_stash_clear.md"),
    ):
        text = _load_markdown_file(_CRITERIA_DIR / filename)
        registry.register(
            ApprovalCriteria(
                tool_id=tool_id,
                action=action,
                criteria_text=text,
                metadata={"file": filename},
            )
        )
    return registry


__all__ = ["ApprovalCriteriaRegistry", "default_criteria_registry"]
