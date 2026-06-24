from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


_READ_CACHEABLE_TOOLS = frozenset(
    {
        "file.read",
        "file.read_range",
        "code.grep",
        "file.list_dir",
        "file.find",
        "file_read",
    }
)

_WRITE_TOOLS = frozenset(
    {
        "file.write",
        "code.patch",
        "file.edit",
        "file.trash",
        "file_write",
    }
)


def _args_hash(tool_name: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "args": args}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _extract_paths(args: dict[str, Any]) -> set[str]:
    return {
        value
        for key in ("path", "file", "directory", "target")
        if isinstance((value := args.get(key)), str) and value
    }


@dataclass(slots=True)
class LoopCache:
    _entries: dict[str, Any] = field(default_factory=dict)
    _path_map: dict[str, set[str]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def get(self, tool_name: str, args: dict[str, Any]) -> Any | None:
        if tool_name not in _READ_CACHEABLE_TOOLS:
            self.misses += 1
            return None
        key = _args_hash(tool_name, args)
        result = self._entries.get(key)
        if result is not None:
            self.hits += 1
        else:
            self.misses += 1
        return result

    def put(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        if tool_name not in _READ_CACHEABLE_TOOLS:
            return
        key = _args_hash(tool_name, args)
        self._entries[key] = result
        self._path_map[key] = _extract_paths(args)

    def invalidate_for_write(self, tool_name: str, args: dict[str, Any]) -> None:
        if tool_name not in _WRITE_TOOLS:
            return
        write_paths = _extract_paths(args)
        if not write_paths:
            return
        to_remove = [
            key
            for key, cached_paths in self._path_map.items()
            if cached_paths & write_paths
        ]
        for key in to_remove:
            self._entries.pop(key, None)
            self._path_map.pop(key, None)
