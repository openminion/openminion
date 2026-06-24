from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any


_WRITE_TOOL_NAMES = frozenset(
    {
        "code.patch",
        "file.edit",
        "file.trash",
        "file.write",
    }
)


@dataclass(frozen=True, slots=True)
class CausalBatch:
    groups: tuple[tuple[int, ...], ...]
    ordered_pairs: tuple[tuple[int, int], ...]


def _tool_name(tool_call: Any) -> str:
    return str(getattr(tool_call, "name", "") or "").strip()


def _tool_arguments(tool_call: Any) -> dict[str, Any]:
    arguments = getattr(tool_call, "arguments", {})
    return dict(arguments) if isinstance(arguments, dict) else {}


def _tool_path(tool_call: Any) -> str:
    arguments = _tool_arguments(tool_call)
    for key in ("path", "root"):
        value = str(arguments.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _is_write(tool_call: Any) -> bool:
    return _tool_name(tool_call) in _WRITE_TOOL_NAMES


def _paths_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_path = PurePath(left)
    right_path = PurePath(right)
    if left_path == right_path:
        return True
    return left_path in right_path.parents or right_path in left_path.parents


def _requires_order(left: Any, right: Any) -> bool:
    left_path = _tool_path(left)
    right_path = _tool_path(right)
    if not left_path or not right_path:
        return True
    left_write = _is_write(left)
    right_write = _is_write(right)
    if left_write or right_write:
        return _paths_overlap(left_path, right_path)
    return False


def _group_from_pairs(
    *,
    tool_count: int,
    ordered_pairs: list[tuple[int, int]],
) -> tuple[tuple[int, ...], ...]:
    dependencies: dict[int, set[int]] = {index: set() for index in range(tool_count)}
    successors: dict[int, set[int]] = {index: set() for index in range(tool_count)}
    for earlier, later in ordered_pairs:
        dependencies[later].add(earlier)
        successors[earlier].add(later)

    groups: list[tuple[int, ...]] = []
    remaining = set(range(tool_count))
    while remaining:
        ready = tuple(
            index
            for index in sorted(remaining)
            if not dependencies[index].intersection(remaining)
        )
        if not ready:
            ready = (min(remaining),)
        groups.append(ready)
        for index in ready:
            remaining.discard(index)
            for successor in successors[index]:
                dependencies[successor].discard(index)
    return tuple(groups)


def classify_batch(tool_calls: list[Any]) -> CausalBatch:
    ordered_pairs: list[tuple[int, int]] = []
    for earlier in range(len(tool_calls)):
        for later in range(earlier + 1, len(tool_calls)):
            if _requires_order(tool_calls[earlier], tool_calls[later]):
                ordered_pairs.append((earlier, later))
    return CausalBatch(
        groups=_group_from_pairs(
            tool_count=len(tool_calls),
            ordered_pairs=ordered_pairs,
        ),
        ordered_pairs=tuple(ordered_pairs),
    )
