import fnmatch
import errno
import os
import shutil
import stat
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
    SearchMatch,
    SearchResult,
    WriteResult,
)
from .searching import compile_line_matcher, search_snippet

_LEGACY_AMBIGUOUS_CODE = cast(ErrorCode, "AMBIGUOUS")
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


class LocalStorageBackend:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    @staticmethod
    def _path(path: str) -> Path:
        return Path(path)

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
        target = self._path(path)
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
                    for name in files:
                        entries.append(
                            EntryInfo(
                                name=name,
                                path=str(Path(root) / name),
                                entry_type="file",
                            )
                        )

                    if len(entries) >= max_entries:
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
                    if len(entries) >= max_entries:
                        break
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except ToolRuntimeError:
            raise
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

        return ListResult(entries=entries[:max_entries], count=len(entries))

    def read(
        self,
        path: str,
        *,
        max_chars: int = 12000,
        offset: int = 0,
    ) -> ReadResult:
        target = self._path(path)
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

    def write(
        self,
        path: str,
        content: str,
        *,
        append: bool = False,
        create_dirs: bool = True,
    ) -> WriteResult:
        target = self._path(path)

        try:
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            elif not target.parent.exists():
                raise ToolRuntimeError("NOT_FOUND", "parent directory does not exist")

            flags = os.O_WRONLY | os.O_CREAT
            flags |= os.O_APPEND if append else os.O_TRUNC
            fd = self._open_leaf_no_symlink(target, flags=flags)
            mode = "a" if append else "w"
            with os.fdopen(fd, mode, encoding="utf-8") as handle:
                handle.write(content)
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
        target = self._path(path)
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
                    if fnmatch.fnmatch(name, pattern):
                        full_path = Path(root) / name
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
        except ToolRuntimeError:
            raise
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
        target = self._path(path)
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
                    if len(matches) >= max_matches:
                        break
                    if scanned_files >= FILE_SEARCH_MAX_SCANNED_FILES:
                        truncated = True
                        break
                    candidate = Path(root) / name
                    if not fnmatch.fnmatch(name, file_glob) and not fnmatch.fnmatch(
                        str(candidate), file_glob
                    ):
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
                    scanned_files += 1
                    try:
                        text = raw.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    lines = text.splitlines()
                    for line_no, line in enumerate(lines, start=1):
                        if not _matches(line):
                            continue
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
                        if len(matches) >= max_matches:
                            break
                if len(matches) >= max_matches or truncated:
                    break
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except ToolRuntimeError:
            raise
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
        target = self._path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"file does not exist: {path}")
        if not target.is_file():
            raise ToolRuntimeError("INVALID_ARGUMENT", "path is not a file")

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            raise ToolRuntimeError("EXEC_ERROR", str(exc)) from exc

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

        if dry_run:
            return EditResult(
                path=str(target),
                operations_applied=operations_applied,
                dry_run=True,
                preview=content,
            )

        try:
            target.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise ToolRuntimeError("EXEC_ERROR", str(exc)) from exc

        return EditResult(
            path=str(target),
            operations_applied=operations_applied,
            dry_run=False,
            preview=None,
        )

    def trash(self, path: str) -> bool:
        target = self._path(path)
        if not target.exists():
            raise ToolRuntimeError("NOT_FOUND", f"path does not exist: {path}")

        try:
            if target.is_dir():
                trash_path = Path.home() / ".Trash" / target.name
                counter = 1
                while trash_path.exists():
                    trash_path = Path.home() / ".Trash" / f"{target.name}_{counter}"
                    counter += 1
                shutil.move(str(target), str(trash_path))
            else:
                target.unlink()
        except PermissionError as exc:
            raise ToolRuntimeError("POLICY_DENIED", "permission denied") from exc
        except ToolRuntimeError:
            raise
        except Exception as exc:
            raise ToolRuntimeError(_LEGACY_ERROR_CODE, str(exc)) from exc

        return True

    def exists(self, path: str) -> bool:
        return self._path(path).exists()

    def is_file(self, path: str) -> bool:
        return self._path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return self._path(path).is_dir()
