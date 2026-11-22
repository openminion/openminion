"""Incremental cache — re-parse only files whose hash changed."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, cast, get_args

from openminion.modules.context.repo_map.constants import (
    RMP_PARSER_VERSION_AST_V1,
)
from openminion.modules.context.repo_map.interfaces import RepoMapBuilder
from openminion.modules.context.repo_map.schemas import RepoMap, RepoSymbol, SymbolKind

_VALID_SYMBOL_KINDS: frozenset[str] = frozenset(get_args(SymbolKind))


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()


def _symbol_from_dict(raw: Any) -> RepoSymbol | None:
    if not isinstance(raw, Mapping):
        return None
    path = raw.get("path")
    name = raw.get("name")
    kind = raw.get("kind")
    signature = raw.get("signature", "")
    doc = raw.get("docstring_first_line", "")
    line_number = raw.get("line_number", 0)
    parent_chain = raw.get("parent_chain", ())
    if (
        not isinstance(path, str)
        or not isinstance(name, str)
        or not isinstance(kind, str)
        or kind not in _VALID_SYMBOL_KINDS
        or not isinstance(signature, str)
        or not isinstance(doc, str)
        or isinstance(line_number, bool)
        or not isinstance(line_number, int)
        or not isinstance(parent_chain, (list, tuple))
        or not all(isinstance(item, str) for item in parent_chain)
    ):
        return None
    return RepoSymbol(
        path=path,
        name=name,
        kind=cast(SymbolKind, kind),
        signature=signature,
        docstring_first_line=doc,
        line_number=line_number,
        parent_chain=tuple(parent_chain),
    )


@dataclass
class RepoMapCache:
    """Per-file hash cache; supports incremental refresh."""

    parser_version: str = RMP_PARSER_VERSION_AST_V1
    root: str = ""
    entries: dict[str, str] = field(default_factory=dict)
    cached_symbols: dict[str, list[RepoSymbol]] = field(default_factory=dict)

    def cache_key(self, path: Path) -> tuple[str, str, str]:
        return (str(path), _hash_file(path), self.parser_version)

    def is_fresh(self, path: Path) -> bool:
        key = str(path)
        if key not in self.cached_symbols:
            return False
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

    def refresh(self, root: Path, *, builder: RepoMapBuilder) -> RepoMap:
        """Re-parse changed files and reuse cached symbols for the rest."""

        root = Path(root)
        normalized_root = str(root.resolve())
        builder_version = getattr(builder, "parser_version", None)
        next_version = (
            builder_version
            if isinstance(builder_version, str) and builder_version
            else self.parser_version
        )
        if self.root != normalized_root or next_version != self.parser_version:
            self.entries.clear()
            self.cached_symbols.clear()
            self.root = normalized_root
            self.parser_version = next_version

        parse_file = getattr(builder, "parse_file", None)
        fallback_by_path: dict[str, list[RepoSymbol]] | None = None
        all_symbols: list[RepoSymbol] = []
        parsed_paths: set[str] = set()
        for file in sorted(root.rglob("*.py")):
            key = str(file)
            parsed_paths.add(key)
            if self.is_fresh(file):
                all_symbols.extend(self.cached_symbols[key])
                continue
            relpath = str(file.relative_to(root))
            if callable(parse_file):
                new_for_file = list(parse_file(file, relpath=relpath))
            else:
                if fallback_by_path is None:
                    fallback_by_path = defaultdict(list)
                    for sym in builder.parse(root).symbols:
                        fallback_by_path[sym.path].append(sym)
                new_for_file = list(fallback_by_path.get(relpath, ()))
            self.record(file, new_for_file)
            all_symbols.extend(new_for_file)

        for stale_key in list(set(self.entries) | set(self.cached_symbols)):
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
            "root": self.root,
            "entries": dict(self.entries),
            "cached_symbols": {
                key: [asdict(sym) for sym in symbols]
                for key, symbols in self.cached_symbols.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    @classmethod
    def load_from(cls, path: Path) -> "RepoMapCache":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(data, Mapping):
            return cls()
        raw_entries = data.get("entries")
        entries = (
            {
                key: val
                for key, val in raw_entries.items()
                if isinstance(key, str) and isinstance(val, str)
            }
            if isinstance(raw_entries, Mapping)
            else {}
        )
        cached_symbols: dict[str, list[RepoSymbol]] = {}
        raw_cached = data.get("cached_symbols")
        if isinstance(raw_cached, Mapping):
            for key, raw_list in raw_cached.items():
                if not isinstance(key, str) or not isinstance(raw_list, list):
                    continue
                decoded: list[RepoSymbol] = []
                for item in raw_list:
                    sym = _symbol_from_dict(item)
                    if sym is None:
                        break
                    decoded.append(sym)
                else:
                    cached_symbols[key] = decoded
        raw_version = data.get("parser_version")
        parser_version = (
            raw_version
            if isinstance(raw_version, str) and raw_version
            else RMP_PARSER_VERSION_AST_V1
        )
        raw_root = data.get("root")
        return cls(
            parser_version=parser_version,
            root=raw_root if isinstance(raw_root, str) else "",
            entries=entries,
            cached_symbols=cached_symbols,
        )


__all__ = ["RepoMapCache"]
