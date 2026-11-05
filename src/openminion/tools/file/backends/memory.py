"""In-memory file operations with conditional replacement checks."""

import fnmatch
import hashlib
from threading import RLock
from io import StringIO
from collections.abc import Callable, Mapping
from pathlib import Path

from openminion.modules.tool.errors import ToolRuntimeError

from .protocol import (
    EditOperation,
    EditResult,
    EntryInfo,
    FindResult,
    ListResult,
    MatchInfo,
    ReadResult,
    RangeResult,
    SearchMatch,
    SearchResult,
    WriteResult,
)
from .searching import compile_line_matcher, search_snippet
from .text import (
    check_replacement_precondition,
    edit_content,
    encode_text,
    select_range,
)
from openminion.tools.file.constants import FILE_SEARCH_MAX_SCANNED_FILES


class InMemoryStorageBackend:
    def __init__(
        self,
        *,
        initial_files: Mapping[str, str] | None = None,
        root: str | Path | None = None,
    ) -> None:
        self._write_lock = RLock()
        self._files: dict[str, str] = {}
        self._dirs: set[str] = set()
        if root is not None:
            self._ensure_dir(self._normalize(root))
        for path, content in (initial_files or {}).items():
            self.write(path, content, append=False, create_dirs=True)

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

    @classmethod
    def _has_hidden_component(cls, path: str, root: str) -> bool:
        relative = Path(path).relative_to(root)
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
                if entry == root or not Path(entry).is_relative_to(root):
                    continue
                name = Path(entry).name
                if not include_hidden and self._has_hidden_component(entry, root):
                    continue
                entries.append(EntryInfo(name=name, path=entry, entry_type="directory"))
                if len(entries) > max_entries:
                    break
            if len(entries) <= max_entries:
                for entry in sorted(self._files):
                    if not Path(entry).is_relative_to(root):
                        continue
                    name = Path(entry).name
                    if not include_hidden and self._has_hidden_component(entry, root):
                        continue
                    entries.append(EntryInfo(name=name, path=entry, entry_type="file"))
                    if len(entries) > max_entries:
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
            entries = [*child_dirs, *child_files][: max_entries + 1]
        truncated = len(entries) > max_entries
        visible_entries = entries[:max_entries]
        return ListResult(
            entries=visible_entries,
            count=len(visible_entries),
            truncated=truncated,
        )

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
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
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
            content_sha256=content_sha256,
        )

    def read_range(
        self, path: str, *, start_line: int, end_line: int, max_chars: int
    ) -> RangeResult:
        if not self.is_file(path):
            raise ToolRuntimeError("NOT_FOUND", f"file does not exist: {path}")
        with StringIO(self._files[self._normalize(path)], newline=None) as handle:
            return select_range(
                handle, start_line=start_line, end_line=end_line, max_chars=max_chars
            )

    def write(
        self,
        path: str,
        content: str,
        *,
        append: bool = False,
        create_dirs: bool = True,
        expected_content_sha256: str | None = None,
        expected_version: str | None = None,
    ) -> WriteResult:
        encoded = encode_text(content)
        normalized = self._normalize(path)
        parent = self._normalize(Path(normalized).parent)
        with self._write_lock:
            if expected_version is not None or (
                append and expected_content_sha256 is not None
            ):
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT", "unsupported replacement precondition"
                )
            if expected_content_sha256 is not None:
                if normalized not in self._files:
                    raise ToolRuntimeError(
                        "NOT_FOUND", "file disappeared since readback"
                    )
                check_replacement_precondition(
                    self._files[normalized], expected_content_sha256
                )
            if create_dirs:
                self._ensure_dir(parent)
            elif not self.is_dir(parent):
                raise ToolRuntimeError("NOT_FOUND", "parent directory does not exist")
            existing = self._files.get(normalized, "") if append else ""
            self._files[normalized] = existing + content
        return WriteResult(
            path=normalized,
            bytes_written=len(encoded),
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
            if not Path(entry).is_relative_to(root):
                continue
            name = Path(entry).name
            if not include_hidden and self._has_hidden_component(entry, root):
                continue
            relative_path = Path(entry).relative_to(root).as_posix()
            basename_pattern = pattern.removeprefix("**/")
            if fnmatch.fnmatch(name, basename_pattern) or fnmatch.fnmatch(
                relative_path, pattern
            ):
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

        matches: list[SearchMatch] = []
        scanned_files = 0
        truncated = False
        for entry, content in sorted(self._files.items()):
            if not Path(entry).is_relative_to(root):
                continue
            name = Path(entry).name
            if not include_hidden and self._has_hidden_component(entry, root):
                continue
            relative = Path(entry).relative_to(root).as_posix()
            if not any(
                fnmatch.fnmatch(value, file_glob) for value in (name, entry, relative)
            ):
                continue
            if path_filter is not None and not path_filter(entry):
                continue
            if scanned_files >= FILE_SEARCH_MAX_SCANNED_FILES:
                truncated = True
                break
            scanned_files += 1
            lines = content.splitlines()
            for line_no, line in enumerate(lines, start=1):
                if not _matches(line):
                    continue
                if len(matches) >= max_matches:
                    truncated = True
                    break
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
            if truncated:
                break
        return SearchResult(
            matches=matches,
            count=len(matches),
            scanned_files=scanned_files,
            truncated=truncated,
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
        content = edit_content(content, operations)
        operations_applied = len(operations)

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
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "recoverable trash is unavailable for the memory backend",
        )

    def exists(self, path: str) -> bool:
        normalized = self._normalize(path)
        return normalized in self._files or normalized in self._dirs

    def is_file(self, path: str) -> bool:
        return self._normalize(path) in self._files

    def is_dir(self, path: str) -> bool:
        return self._normalize(path) in self._dirs
