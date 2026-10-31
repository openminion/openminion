import fnmatch
import errno
import os
import re
import stat
from functools import lru_cache
from pathlib import Path
from typing import Callable, cast

from openminion.modules.tool.contracts.schemas import ErrorCode
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.constants import (
    FILE_SEARCH_MAX_READ_BYTES,
    FILE_SEARCH_MAX_SCANNED_FILES,
)

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
from .text import edit_content, encode_text, select_range

_LEGACY_ERROR_CODE = cast(ErrorCode, "ERROR")
_SEARCH_PRUNED_DIRS = frozenset(
    {
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


@lru_cache(maxsize=128)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob ``pattern`` into an anchored regular expression.

    ``**/`` maps to ``(?:.*/)?`` (zero or more leading directories), ``**``
    to ``.*``, ``*`` to ``[^/]*`` so a single star never crosses a directory
    boundary, ``?`` to ``[^/]``, and every other character is escaped
    literally. The result is anchored with ``^`` and ``$`` so a path pattern
    matches the whole root-relative path rather than any trailing suffix.
    """
    tokens: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            tokens.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            tokens.append(".*")
            i += 2
        elif pattern[i] == "*":
            tokens.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            tokens.append("[^/]")
            i += 1
        else:
            tokens.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(tokens) + "$")


def _matches_glob(pattern: str, name: str, relative_path: str) -> bool:
    """Return whether ``name`` matches ``pattern``.

    ``relative_path`` is the slash-separated path relative to the search
    root. Basename-only patterns (no separator) match ``name`` with
    :func:`fnmatch.fnmatch`, so they keep recursing across directories as
    before. Patterns containing a separator are translated to a regex (see
    :func:`_glob_to_regex`) and matched against ``relative_path``, giving
    root-relative anchoring, single-star directory boundaries, recursive
    ``**`` depth, and a leading ``**/`` directory prefix.
    """
    if "/" not in pattern:
        return fnmatch.fnmatch(name, pattern)
    return _glob_to_regex(pattern).match(relative_path) is not None


class LocalStorageBackend:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    @staticmethod
    def _open_leaf_no_symlink(
        target: Path,
        *,
        flags: int,
        mode: int = 0o666,
    ) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags | nofollow, mode)
        try:
            stat_result = os.fstat(fd)
            if not stat.S_ISREG(stat_result.st_mode):
                raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a file")
            return fd
        except Exception:
            os.close(fd)
            raise

    def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> ListResult:
        target = Path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not target.is_dir():
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a directory")

        entries: list[EntryInfo] = []
        try:
            if recursive:
                for root, dirs, files in os.walk(target):
                    if not include_hidden:
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        files = [f for f in files if not f.startswith(".")]

                    for name in dirs:
                        entries.append(
                            EntryInfo(
                                name=name,
                                path=str(Path(root) / name),
                                entry_type="directory",
                            )
                        )
                        if len(entries) > max_entries:
                            break
                    if len(entries) > max_entries:
                        break
                    for name in files:
                        entries.append(
                            EntryInfo(
                                name=name,
                                path=str(Path(root) / name),
                                entry_type="file",
                            )
                        )
                        if len(entries) > max_entries:
                            break

                    if len(entries) > max_entries:
                        break
            else:
                for item in target.iterdir():
                    if not include_hidden and item.name.startswith("."):
                        continue
                    entries.append(
                        EntryInfo(
                            name=item.name,
                            path=str(item),
                            entry_type="directory" if item.is_dir() else "file",
                        )
                    )
                    if len(entries) > max_entries:
                        break
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

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
        target = Path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not target.is_file():
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a file")

        try:
            fd = self._open_leaf_no_symlink(target, flags=os.O_RDONLY)
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                content = handle.read()
        except UnicodeDecodeError as exc:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "file is not text-encoded"
            ) from exc
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise ToolRuntimeError(
                    "POLICY_DENIED", "refusing to follow symlink leaf"
                ) from exc
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc
        except ToolRuntimeError:
            raise
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

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

    def read_range(
        self, path: str, *, start_line: int, end_line: int, max_chars: int
    ) -> RangeResult:
        try:
            fd = self._open_leaf_no_symlink(Path(path), flags=os.O_RDONLY)
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                return select_range(
                    handle,
                    start_line=start_line,
                    end_line=end_line,
                    max_chars=max_chars,
                )
        except UnicodeDecodeError as exc:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "file is not text-encoded"
            ) from exc
        except FileNotFoundError as exc:
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}") from exc
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except OSError as exc:
            raise ToolRuntimeError("EXEC_ERROR", str(exc)) from exc

    def write(
        self,
        path: str,
        content: str,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> WriteResult:
        target = Path(path)
        encoded = encode_text(content)

        try:
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            elif not target.parent.exists():
                raise ToolRuntimeError("NOT_FOUND", "parent directory does not exist")

            flags = os.O_WRONLY | os.O_CREAT
            flags |= os.O_APPEND if append else os.O_TRUNC
            fd = self._open_leaf_no_symlink(target, flags=flags)
            mode = "ab" if append else "wb"
            with os.fdopen(fd, mode) as handle:
                handle.write(encoded)
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise ToolRuntimeError(
                    "POLICY_DENIED", "refusing to follow symlink leaf"
                ) from exc
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc
        except ToolRuntimeError:
            raise
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

        return WriteResult(
            path=str(target),
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
        target = Path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not target.is_dir():
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a directory")

        matches: list[MatchInfo] = []
        try:
            for root, dirs, files in os.walk(target):
                if not include_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]

                for name in files:
                    full_path = Path(root) / name
                    relative_path = full_path.relative_to(target).as_posix()
                    if _matches_glob(pattern, name, relative_path):
                        matches.append(
                            MatchInfo(
                                name=name,
                                path=str(full_path),
                                size=full_path.stat().st_size
                                if full_path.exists()
                                else 0,
                            )
                        )
                        if len(matches) >= max_entries:
                            break
                if len(matches) >= max_entries:
                    break
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

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
        target = Path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")
        if not target.is_dir():
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a directory")

        _matches = compile_line_matcher(
            query,
            regex=regex,
            case_sensitive=case_sensitive,
        )

        matches: list[SearchMatch] = []
        scanned_files = 0
        truncated = False
        try:
            for root, dirs, files in os.walk(target):
                if not include_hidden:
                    dirs[:] = [
                        name
                        for name in dirs
                        if not name.startswith(".") and name not in _SEARCH_PRUNED_DIRS
                    ]
                    files = [name for name in files if not name.startswith(".")]
                else:
                    dirs[:] = [name for name in dirs if name not in _SEARCH_PRUNED_DIRS]
                for name in files:
                    candidate = Path(root) / name
                    relative_path = candidate.relative_to(target).as_posix()
                    if not _matches_glob(file_glob, name, relative_path):
                        continue
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                    if path_filter is not None and not path_filter(str(candidate)):
                        continue
                    try:
                        raw = candidate.read_bytes()
                    except OSError:
                        continue
                    if len(raw) > FILE_SEARCH_MAX_READ_BYTES:
                        continue
                    if b"\x00" in raw:
                        continue
                    if scanned_files >= FILE_SEARCH_MAX_SCANNED_FILES:
                        truncated = True
                        break
                    scanned_files += 1
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.splitlines()
                    for line_no, line in enumerate(lines, start=1):
                        if not _matches(line):
                            continue
                        if len(matches) >= max_matches:
                            truncated = True
                            break
                        matches.append(
                            SearchMatch(
                                path=str(candidate),
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
                if truncated:
                    break
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc
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
        target = Path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"file does not exist: {path}")
        if not target.is_file():
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a file")

        try:
            with target.open(encoding="utf-8", newline="") as handle:
                content = handle.read()
        except (OSError, UnicodeError) as exc:
            raise ToolRuntimeError("EXEC_ERROR", str(exc)) from exc

        content = edit_content(content, operations)
        operations_applied = len(operations)

        if dry_run:
            return EditResult(
                path=str(target),
                operations_applied=operations_applied,
                dry_run=True,
                preview=content,
            )

        try:
            self.write(path, content, create_dirs=False)
        except (OSError, UnicodeError) as exc:
            raise ToolRuntimeError("EXEC_ERROR", str(exc)) from exc

        return EditResult(
            path=str(target),
            operations_applied=operations_applied,
            dry_run=False,
            preview=None,
        )

    def trash(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")

        try:
            from send2trash import send2trash

            send2trash(str(target))
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

        return True

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        return Path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()
