from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class EntryInfo:
    name: str
    path: str
    entry_type: str
    size: int = 0


@dataclass(frozen=True)
class MatchInfo:
    name: str
    path: str
    size: int = 0


@dataclass(frozen=True)
class ReadResult:
    content: str
    total_length: int
    returned_length: int
    truncated: bool


@dataclass(frozen=True)
class WriteResult:
    path: str
    bytes_written: int
    mode: str


@dataclass(frozen=True)
class FindResult:
    matches: list[MatchInfo]
    count: int


@dataclass(frozen=True)
class ListResult:
    entries: list[EntryInfo]
    count: int


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line: int
    snippet: str


@dataclass(frozen=True)
class SearchResult:
    matches: list[SearchMatch]
    count: int
    scanned_files: int
    truncated: bool = False


@dataclass(frozen=True)
class EditOperation:
    op: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class EditResult:
    path: str
    operations_applied: int
    dry_run: bool
    preview: str | None = None


@runtime_checkable
class StorageBackend(Protocol):
    def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> ListResult: ...

    def read(
        self,
        path: str,
        *,
        max_chars: int = 12000,
        offset: int = 0,
    ) -> ReadResult: ...

    def write(
        self,
        path: str,
        content: str,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> WriteResult: ...

    def find(
        self,
        path: str,
        *,
        pattern: str = "*",
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> FindResult: ...

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
    ) -> SearchResult: ...

    def edit(
        self,
        path: str,
        operations: list[EditOperation],
        *,
        dry_run: bool = False,
    ) -> EditResult: ...

    def trash(self, path: str) -> bool: ...

    def exists(self, path: str) -> bool: ...

    def is_file(self, path: str) -> bool: ...

    def is_dir(self, path: str) -> bool: ...
