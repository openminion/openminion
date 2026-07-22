import fnmatch
from pathlib import Path
from typing import cast
from collections.abc import Callable, Mapping

from openminion.modules.tool.contracts.schemas import ErrorCode
from openminion.modules.tool.errors import ToolRuntimeError

from .protocol import (
    EditOperation,
    EditResult,
    EntryInfo,
    FindResult,
    ListResult,
    MatchInfo,
    ReadResult,
    SearchMatch,
    SearchResult,
    WriteResult,
)
from .searching import compile_line_matcher, search_snippet

_LEGACY_AMBIGUOUS_CODE = cast(ErrorCode, "AMBIGUOUS")


class InMemoryStorageBackend:
    def __init__(
        self,
        *,
        initial_files: Mapping[str, str] | None = None,
        root: str | Path | None = None,
    ) -> None:
        self._files: dict[str, str] = {}
        self._dirs: set[str] = set()
        if root is not None:
            self._ensure_dir(self._normalize(root))
        for path, content in (initial_files or {}).items():
            self.write(str(path), str(content), append=False, create_dirs=True)

    @staticmethod
    def _normalize(path: str | Path) -> str:
        return str(Path(path))

    def _ensure_dir(self, path: str) -> None:
        current = self._normalize(path)
        while True:
            self._dirs.add(current)
            parent = self._normalize(Path(current).parent)
            if parent == current:
                break
            current = parent

    @staticmethod
    def _is_relative_to(path: str, root: str) -> bool:
        try:
            Path(path).relative_to(Path(root))
            return True
        except ValueError:
            return False

    @classmethod
    def _has_hidden_component(cls, path: str, root: str) -> bool:
        try:
            relative = Path(path).relative_to(Path(root))
        except ValueError:
            return False
        return any(part.startswith(".") for part in relative.parts)

    def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> ListResult:
        root = self._normalize(path)
        if not self.exists(root):
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not self.is_dir(root):
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a directory")

        entries: list[EntryInfo] = []
        if recursive:
            for entry in sorted(self._dirs):
                if entry == root or not self._is_relative_to(entry, root):
                    continue
                name = Path(entry).name
                if not include_hidden and self._has_hidden_component(entry, root):
                    continue
                entries.append(EntryInfo(name=name, path=entry, entry_type="directory"))
                if len(entries) >= max_entries:
                    break
            if len(entries) < max_entries:
                for entry in sorted(self._files):
                    if not self._is_relative_to(entry, root):
                        continue
                    name = Path(entry).name
                    if not include_hidden and self._has_hidden_component(entry, root):
                        continue
                    entries.append(EntryInfo(name=name, path=entry, entry_type="file"))
                    if len(entries) >= max_entries:
                        break
        else:
            child_dirs: list[EntryInfo] = []
            child_files: list[EntryInfo] = []
            for entry in sorted(self._dirs):
                if entry == root:
                    continue
                parent = self._normalize(Path(entry).parent)
                if parent != root:
                    continue
                name = Path(entry).name
                if not include_hidden and name.startswith("."):
                    continue
                child_dirs.append(
                    EntryInfo(name=name, path=entry, entry_type="directory")
                )
            for entry in sorted(self._files):
                parent = self._normalize(Path(entry).parent)
                if parent != root:
                    continue
                name = Path(entry).name
                if not include_hidden and name.startswith("."):
                    continue
                child_files.append(EntryInfo(name=name, path=entry, entry_type="file"))
            entries = [*child_dirs, *child_files][:max_entries]
        return ListResult(entries=entries[:max_entries], count=len(entries))

    def read(
        self,
        path: str,
        *,
        max_chars: int = 12000,
        offset: int = 0,
    ) -> ReadResult:
        normalized = self._normalize(path)
        if not self.exists(normalized):
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not self.is_file(normalized):
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a file")

        content = self._files[normalized]
        total_length = len(content)
        if offset > 0:
            if offset >= total_length:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    "offset beyond file length",
                )
            content = content[offset:]

        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return ReadResult(
            content=content,
            total_length=total_length,
            returned_length=len(content),
            truncated=truncated,
        )

    def write(
        self,
        path: str,
        content: str,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> WriteResult:
        normalized = self._normalize(path)
        parent = self._normalize(Path(normalized).parent)
        if create_dirs:
            self._ensure_dir(parent)
        elif not self.is_dir(parent):
            raise ToolRuntimeError("NOT_FOUND", "parent directory does not exist")

        existing = self._files.get(normalized, "") if append else ""
        self._files[normalized] = existing + content
        self._ensure_dir(parent)
        return WriteResult(
            path=normalized,
            bytes_written=len(content.encode("utf-8")),
            mode="append" if append else "write",
        )

    def find(
        self,
        path: str,
        *,
        pattern: str = "*",
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> FindResult:
        root = self._normalize(path)
        if not self.exists(root):
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not self.is_dir(root):
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a directory")

        matches: list[MatchInfo] = []
        for entry, content in sorted(self._files.items()):
            if not self._is_relative_to(entry, root):
                continue
            name = Path(entry).name
            if not include_hidden and self._has_hidden_component(entry, root):
                continue
            if fnmatch.fnmatch(name, pattern):
                matches.append(
                    MatchInfo(name=name, path=entry, size=len(content.encode("utf-8")))
                )
                if len(matches) >= max_entries:
                    break
        return FindResult(matches=matches[:max_entries], count=len(matches))

    def search(
        self,
        path: str,
        *,
        query: str,
        regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 0,
        max_matches: int = 100,
        include_hidden: bool = False,
        file_glob: str = "**/*",
        path_filter: Callable[[str], bool] | None = None,
    ) -> SearchResult:
        root = self._normalize(path)
        if not self.exists(root):
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not self.is_dir(root):
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a directory")

        _matches = compile_line_matcher(
            query,
            regex=regex,
            case_sensitive=case_sensitive,
        )

        glob_name = Path(file_glob).name or "*"
        matches: list[SearchMatch] = []
        scanned_files = 0
        for entry, content in sorted(self._files.items()):
            if len(matches) >= max_matches:
                break
            if not self._is_relative_to(entry, root):
                continue
            name = Path(entry).name
            if not include_hidden and self._has_hidden_component(entry, root):
                continue
            if not fnmatch.fnmatchcase(name, glob_name):
                continue
            if path_filter is not None and not path_filter(entry):
                continue
            scanned_files += 1
            lines = content.splitlines()
            for line_no, line in enumerate(lines, start=1):
                if not _matches(line):
                    continue
                matches.append(
                    SearchMatch(
                        path=entry,
                        line=line_no,
                        snippet=search_snippet(
                            lines,
                            line_no=line_no,
                            line=line,
                            context_lines=context_lines,
                        ),
                    )
                )
                if len(matches) >= max_matches:
                    break
        return SearchResult(
            matches=matches,
            count=len(matches),
            scanned_files=scanned_files,
        )

    def edit(
        self,
        path: str,
        operations: list[EditOperation],
        *,
        dry_run: bool = False,
    ) -> EditResult:
        normalized = self._normalize(path)
        if not self.exists(normalized):
            raise ToolRuntimeError("NOT_FOUND", f"file does not exist: {path}")
        if not self.is_file(normalized):
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a file")

        content = self._files[normalized]
        operations_applied = 0
        for operation in operations:
            old_text = operation.old_text
            new_text = operation.new_text
            count = content.count(old_text)
            if count == 0:
                raise ToolRuntimeError(
                    "NOT_FOUND",
                    f"anchor not found: {old_text[:60]!r}",
                )
            if count > 1:
                raise ToolRuntimeError(
                    _LEGACY_AMBIGUOUS_CODE,
                    f"anchor matches {count} times: {old_text[:60]!r}",
                )
            if operation.op == "replace":
                content = content.replace(old_text, new_text, 1)
            elif operation.op == "insert_before":
                content = content.replace(old_text, new_text + "\n" + old_text, 1)
            elif operation.op == "insert_after":
                content = content.replace(old_text, old_text + "\n" + new_text, 1)
            else:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"unknown edit operation: {operation.op}",
                )
            operations_applied += 1

        if not dry_run:
            self._files[normalized] = content
        return EditResult(
            path=normalized,
            operations_applied=operations_applied,
            dry_run=dry_run,
            preview=content if dry_run else None,
        )

    def trash(self, path: str) -> bool:
        normalized = self._normalize(path)
        if not self.exists(normalized):
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if self.is_file(normalized):
            del self._files[normalized]
            return True

        to_delete_files = [
            entry for entry in self._files if self._is_relative_to(entry, normalized)
        ]
        for entry in to_delete_files:
            del self._files[entry]

        to_delete_dirs = [
            entry
            for entry in self._dirs
            if entry == normalized or self._is_relative_to(entry, normalized)
        ]
        for entry in to_delete_dirs:
            self._dirs.discard(entry)
        return True

    def exists(self, path: str) -> bool:
        normalized = self._normalize(path)
        return normalized in self._files or normalized in self._dirs

    def is_file(self, path: str) -> bool:
        return self._normalize(path) in self._files

    def is_dir(self, path: str) -> bool:
        return self._normalize(path) in self._dirs
