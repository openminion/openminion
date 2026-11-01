"""Local file operations with guarded replacement on an open file descriptor."""

import fnmatch
import hashlib
import errno
import os
import re
import stat
from functools import lru_cache
from pathlib import Path
from types import ModuleType
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
from .text import (
    check_replacement_precondition,
    edit_content,
    encode_text,
    select_range,
)

fcntl: ModuleType | None
try:
    import fcntl
except ImportError:
    fcntl = None


def _file_version(stat_result: os.stat_result) -> str:
    return ":".join(
        str(value)
        for value in (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )
    )


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


def _find_char_class_end(pattern: str, start: int) -> int | None:
    """Return the index of the ``]`` closing a glob class at ``start``.

    A ``!`` immediately after ``[`` is a negation marker, and a ``]``
    immediately after ``[`` (or after the marker) is a literal ``]``,
    matching fnmatch. ``^`` is *not* a marker: fnmatch keeps it as a literal
    inside the class. Returns ``None`` for an unterminated class, which the
    caller then treats as a literal ``[``.
    """
    j = start + 1
    if j < len(pattern) and pattern[j] == "!":
        j += 1
    if j < len(pattern) and pattern[j] == "]":
        j += 1
    while j < len(pattern) and pattern[j] != "]":
        j += 1
    return j if j < len(pattern) else None


def _escape_class_lead(body: str) -> str:
    """Keep a leading ``]`` literal inside a negated class body."""
    return "\\" + body if body.startswith("]") else body


def _translate_char_class(body: str) -> str:
    """Translate a fnmatch ``[...]`` body into a regex character class.

    Mirrors :func:`fnmatch.translate` exactly: ``!`` is the only negation
    operator (``^`` stays a literal), ``a-z`` ranges are preserved, and an
    invalid/descending range such as ``z-a`` collapses to 'never match'
    instead of raising. An empty negated body matches any non-separator
    character; an empty plain body never matches.
    """
    stuff = body
    if "-" not in stuff:
        stuff = stuff.replace("\\", r"\\")
    else:
        chunks: list[str] = []
        i = 0
        j = len(body)
        k = 2 if body.startswith("!") else 1
        while True:
            k = body.find("-", k, j)
            if k < 0:
                break
            chunks.append(body[i:k])
            i = k + 1
            k = k + 3
        chunk = body[i:j]
        if chunk:
            chunks.append(chunk)
        else:
            chunks[-1] += "-"
        for k in range(len(chunks) - 1, 0, -1):
            if chunks[k - 1][-1] > chunks[k][0]:
                chunks[k - 1] = chunks[k - 1][:-1] + chunks[k][1:]
                del chunks[k]
        stuff = "-".join(s.replace("\\", r"\\").replace("-", r"\-") for s in chunks)
    stuff = re.sub(r"([&~|])", r"\\\1", stuff)

    if not stuff:
        return "(?!)"
    if stuff == "!":
        return "[^/]"
    if stuff[0] == "!":
        # The negation must not consume the path separator, or `a[!x]b` would
        # match `a/b`; fnmatch has no separator concept, a path matcher does.
        stuff = "^/" + _escape_class_lead(stuff[1:])
    elif stuff[0] in ("^", "["):
        stuff = "\\" + stuff
    class_body = "[" + stuff + "]"
    # A positive class can still cover the separator through a range such as
    # `[.-0]`, which would let one component pattern match across directories.
    if re.fullmatch(class_body, "/"):
        return "(?!/)" + class_body
    return class_body


@lru_cache(maxsize=128)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob ``pattern`` into an anchored regular expression.

    ``**/`` maps to ``(?:.*/)?`` (zero or more leading directories), ``**``
    to ``.*``, ``*`` to ``[^/]*`` so a single star never crosses a directory
    boundary, ``?`` to ``[^/]``, and a ``[...]`` character class keeps its
    fnmatch semantics: ``!`` negates, ``^`` stays literal, ``a-z`` ranges are
    preserved, and neither negation nor a range may consume the path
    separator. Every other character is escaped literally. The result is
    anchored with ``^``/``\\Z`` and compiled with ``re.S``, so a path pattern
    matches the whole root-relative path — including directory names that
    contain a newline — rather than any trailing suffix.
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
        elif pattern[i] == "[":
            end = _find_char_class_end(pattern, i)
            if end is None:
                tokens.append(r"\[")
                i += 1
            else:
                tokens.append(_translate_char_class(pattern[i + 1 : end]))
                i = end + 1
        else:
            tokens.append(re.escape(pattern[i]))
            i += 1
    # DOTALL so ``*``/``**`` also match a name containing a newline, and ``\Z``
    # so a trailing newline cannot satisfy the anchor.
    return re.compile("^" + "".join(tokens) + r"\Z", re.S)


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
            with os.fdopen(fd, "r", encoding="utf-8", newline="") as handle:
                content = handle.read()
                version = _file_version(os.fstat(handle.fileno()))
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

        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        content = content.replace("\r\n", "\n").replace("\r", "\n")
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
            version=version,
            content_sha256=content_sha256,
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
        expected_content_sha256: str | None = None,
        expected_version: str | None = None,
    ) -> WriteResult:
        target = Path(path)
        encoded = encode_text(content)

        try:
            if (
                expected_content_sha256 is not None or expected_version is not None
            ) and not target.exists():
                raise ToolRuntimeError("NOT_FOUND", "file disappeared since readback")
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            elif not target.parent.exists():
                raise ToolRuntimeError("NOT_FOUND", "parent directory does not exist")

            self._write_content(
                target,
                encoded,
                append=append,
                expected_content_sha256=expected_content_sha256,
                expected_version=expected_version,
            )
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

    def _write_content(
        self,
        target: Path,
        encoded: bytes,
        *,
        append: bool,
        expected_content_sha256: str | None,
        expected_version: str | None,
    ) -> None:
        guarded = expected_content_sha256 is not None or expected_version is not None
        if guarded and append:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "conditional append is not supported"
            )
        if guarded and fcntl is None:
            raise ToolRuntimeError(
                "PLATFORM_UNSUPPORTED", "conditional replacement requires file locking"
            )
        flags = os.O_RDWR if guarded else os.O_WRONLY | os.O_CREAT
        fd = self._open_leaf_no_symlink(
            target, flags=flags | (os.O_APPEND if append else 0)
        )
        with os.fdopen(fd, "r+b" if guarded else "wb") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if guarded:
                if expected_content_sha256 is not None:
                    check_replacement_precondition(
                        handle.read(), expected_content_sha256
                    )
                current = os.fstat(handle.fileno())
                if (
                    expected_version is not None
                    and _file_version(current) != expected_version
                    or current.st_ino != target.stat(follow_symlinks=False).st_ino
                ):
                    raise ToolRuntimeError(
                        "PRECONDITION_FAILED", "file version changed since readback"
                    )
                handle.seek(0)
            if not append:
                handle.truncate(0)
            handle.write(encoded)

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
