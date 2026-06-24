"""Incremental cache — re-parse only files whose hash changed."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from openminion.modules.context.repo_map.constants import (
    RMP_PARSER_VERSION_AST_V1,
)
from openminion.modules.context.repo_map.schemas import RepoMap, RepoSymbol


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()


@dataclass
class RepoMapCache:
    """Per-file hash cache; supports incremental refresh."""

    parser_version: str = RMP_PARSER_VERSION_AST_V1
    entries: dict[str, str] = field(default_factory=dict)
    cached_symbols: dict[str, list[RepoSymbol]] = field(default_factory=dict)

    def cache_key(self, path: Path) -> tuple[str, str, str]:
        return (str(path), _hash_file(path), self.parser_version)

    def is_fresh(self, path: Path) -> bool:
        key = str(path)
        stored = self.entries.get(key, "")
        current = _hash_file(path)
        return bool(stored) and stored == current

    def invalidate(self, path: Path) -> None:
        self.entries.pop(str(path), None)
        self.cached_symbols.pop(str(path), None)

    def record(self, path: Path, symbols: list[RepoSymbol]) -> None:
        key = str(path)
        self.entries[key] = _hash_file(path)
        self.cached_symbols[key] = list(symbols)

    def refresh(
        self, root: Path, *, builder
    ) -> RepoMap:  # `builder` is RepoMapBuilder duck-typed
        """Re-parse only files whose hash changed; reuse cached symbols
        for unchanged files."""

        root = Path(root)
        all_symbols: list[RepoSymbol] = []
        parsed_paths: set[str] = set()
        for file in sorted(root.rglob("*.py")):
            key = str(file)
            parsed_paths.add(key)
            if self.is_fresh(file):
                all_symbols.extend(self.cached_symbols.get(key, []))
                continue
            # Re-parse this file via the builder; isolate per-file errors.
            single_map = (
                builder.parse(file.parent)
                if file.parent != root
                else builder.parse(root)
            )
            # Filter symbols whose path matches this file (relative to root)
            try:
                relpath = str(file.relative_to(root))
            except ValueError:
                relpath = str(file)
            new_for_file = [s for s in single_map.symbols if s.path == relpath]
            self.record(file, new_for_file)
            all_symbols.extend(new_for_file)
        # Drop stale entries for files no longer present.
        for stale_key in list(self.entries.keys()):
            if stale_key not in parsed_paths:
                self.entries.pop(stale_key, None)
                self.cached_symbols.pop(stale_key, None)
        return RepoMap(
            root=str(root),
            symbols=tuple(all_symbols),
            parser_version=self.parser_version,
        )

    def save_to(self, path: Path) -> None:
        payload = {
            "parser_version": self.parser_version,
            "entries": dict(self.entries),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    @classmethod
    def load_from(cls, path: Path) -> "RepoMapCache":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            parser_version=str(data.get("parser_version", RMP_PARSER_VERSION_AST_V1)),
            entries=dict(data.get("entries", {})),
        )


__all__ = ["RepoMapCache"]
