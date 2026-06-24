from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.base.errors.adapt import (
    error_dict_from_exception,
    error_dict_from_mapping,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_FILE_EDIT,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_READ_RANGE,
    MODEL_FILE_SEARCH,
    MODEL_FILE_TRASH,
    MODEL_FILE_WRITE,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.config import resolve_tool_workspace_root

from .backends import (
    EditOperation,
    EntryInfo,
    InMemoryStorageBackend,
    LocalStorageBackend,
    MatchInfo,
    SearchMatch,
    StorageBackend,
)
from .constants import (
    FILE_BACKEND_LOCAL,
    FILE_BACKEND_MEMORY,
    FILE_DEFAULT_MAX_ENTRIES,
    FILE_DEFAULT_MAX_READ_CHARS,
    FILE_MAX_ENTRIES,
    FILE_MAX_READ_CHARS,
    FILE_MAX_WRITE_CHARS,
)


_FILE_TOOL_SOURCE = "file_module"


def _normalize_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _collapse_alias_group(
    data: dict[str, Any],
    canonical: str,
    aliases: tuple[str, ...],
) -> None:
    if canonical not in data:
        for alias in aliases:
            if alias in data:
                data[canonical] = data.pop(alias)
                break
    canonical_value = data.get(canonical)
    for alias in aliases:
        if alias in data and data.get(alias) == canonical_value:
            data.pop(alias, None)


class FileListDirArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = Field(default=".", description="Directory path")
    recursive: bool = Field(default=False, description="List recursively")
    max_entries: int = Field(
        default=FILE_DEFAULT_MAX_ENTRIES, ge=1, le=FILE_MAX_ENTRIES
    )
    include_hidden: bool = Field(default=False, description="Include hidden files")

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."


class FileReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File path",
        validation_alias=AliasChoices("path", "file_path", "filename"),
    )
    max_chars: int = Field(
        default=FILE_DEFAULT_MAX_READ_CHARS, ge=1, le=FILE_MAX_READ_CHARS
    )
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _collapse_compatible_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _collapse_alias_group(data, "path", ("file_path", "filename"))
        return data

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized


class FileReadRangeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, description="File path")
    start_line: int = Field(
        default=1,
        ge=1,
        description="1-based first line",
        validation_alias=AliasChoices("start_line", "startLine"),
    )
    end_line: int = Field(
        default=200,
        ge=1,
        description="1-based last line",
        validation_alias=AliasChoices("end_line", "endLine"),
    )

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized

    @model_validator(mode="after")
    def _validate_range(self) -> "FileReadRangeArgs":
        if self.start_line > self.end_line:
            raise ValueError("start_line must be <= end_line")
        return self


class FileWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="File path",
        validation_alias=AliasChoices("path", "filename", "file_path", "destination"),
    )
    content: str = Field(
        default="",
        description="Content to write",
        validation_alias=AliasChoices("content", "text", "body"),
    )
    append: bool = Field(default=False, description="Append to existing file")
    create_dirs: bool = Field(
        default=True, description="Create parent directories if missing"
    )

    @model_validator(mode="before")
    @classmethod
    def _collapse_compatible_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _collapse_alias_group(data, "path", ("filename", "file_path", "destination"))
        _collapse_alias_group(data, "content", ("text", "body"))
        return data

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized


class FileFindArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = Field(default=".", description="Directory to search")
    pattern: str = Field(default="*", description="Glob pattern")
    max_entries: int = Field(
        default=FILE_DEFAULT_MAX_ENTRIES, ge=1, le=FILE_MAX_ENTRIES
    )
    include_hidden: bool = Field(default=False, description="Include hidden files")

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."

    @field_validator("pattern", mode="before")
    @classmethod
    def _normalize_pattern(cls, value: Any) -> str:
        return str(value or "*").strip() or "*"


class FileTrashArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, description="Path to trash")

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized


class FileSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = Field(default=".", description="Directory to search")
    query: str = Field(..., min_length=1, description="Search query")
    regex: bool = Field(default=False, description="Use regex matching")
    case_sensitive: bool = Field(default=False, description="Case-sensitive matching")
    context_lines: int = Field(
        default=0, ge=0, le=5, description="Lines of context around each match"
    )
    max_matches: int = Field(default=100, ge=1, le=500)
    include_hidden: bool = Field(default=False, description="Include hidden files")
    file_glob: str = Field(default="**/*", description="Glob pattern to filter files")

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."


class FileEditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["replace", "insert_before", "insert_after"] = Field(
        ..., description="Edit operation type"
    )
    old_text: str = Field(..., min_length=1, description="Exact anchor text to locate")
    new_text: str = Field(default="", description="Replacement or inserted text")


class FileEditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, description="File path to edit")
    operations: List[FileEditOperation] = Field(..., min_length=1)
    dry_run: bool = Field(default=False, description="Preview changes without writing")

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized


_backend_cache: dict[tuple[str, str], StorageBackend] = {}


def _resolve_workspace_root(ctx: RuntimeContext) -> Path:
    runtime_env = getattr(ctx, "env", None) or {}
    explicit_workspace = (
        str(runtime_env.get("OPENMINION_WORKSPACE_ROOT", "") or "").strip()
        or str(runtime_env.get("OPENMINION_WORKSPACE", "") or "").strip()
    )
    if explicit_workspace:
        return Path(explicit_workspace).expanduser().resolve(strict=False)

    env_workspace = resolve_tool_workspace_root(env=runtime_env, fallback="")
    if str(env_workspace) != str(Path.cwd().resolve(strict=False)):
        return env_workspace
    raw = getattr(ctx.policy, "raw", {})
    workspace_root = raw.get("workspace_root")
    if workspace_root:
        return Path(workspace_root).expanduser().resolve(strict=False)
    return Path(ctx.workspace).expanduser().resolve(strict=False)


def _resolve_relative_base_dir(ctx: RuntimeContext) -> Path:
    workspace_root = _resolve_workspace_root(ctx)
    raw = getattr(ctx.policy, "raw", {})
    context_metadata = raw.get("context_metadata", {}) if isinstance(raw, dict) else {}
    candidate = str(context_metadata.get("cwd", "") or "").strip()
    if not candidate:
        return workspace_root
    resolved_candidate = Path(candidate).expanduser().resolve(strict=False)
    try:
        resolved_candidate.relative_to(workspace_root)
    except ValueError:
        return workspace_root
    return resolved_candidate


def _resolve_path_lexical(ctx: RuntimeContext, raw_path: str, operation: str) -> str:
    workspace_root = _resolve_workspace_root(ctx)
    relative_base_dir = _resolve_relative_base_dir(ctx)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        parts = candidate.parts
        if parts and parts[0] == workspace_root.name:
            candidate = Path(*parts[1:]) if len(parts) > 1 else Path(".")
        candidate = relative_base_dir / candidate
    resolved = candidate.resolve(strict=False)

    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            f"path escapes workspace root: {raw_path}",
        )

    ctx.policy.ensure_path_allowed(
        str(resolved),
        workspace=workspace_root,
        operation=operation,
    )
    return str(resolved)


def _get_backend(ctx: RuntimeContext) -> StorageBackend:
    raw = getattr(ctx.policy, "raw", {})
    backend_type = str(
        raw.get("file_backend", FILE_BACKEND_LOCAL) or FILE_BACKEND_LOCAL
    )
    cache_key = (backend_type, str(ctx.run_root))
    cached = _backend_cache.get(cache_key)
    if cached is not None:
        return cached

    workspace_root = _resolve_workspace_root(ctx)
    if backend_type == FILE_BACKEND_LOCAL:
        backend: StorageBackend = LocalStorageBackend(workspace_root)
    elif backend_type == FILE_BACKEND_MEMORY:
        backend = InMemoryStorageBackend(root=workspace_root)
    else:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", f"unknown file backend: {backend_type}"
        )
    _backend_cache[cache_key] = backend
    return backend


def _reset_backend_cache_for_tests() -> None:
    _backend_cache.clear()


def _entry_to_payload(entry: EntryInfo) -> Dict[str, Any]:
    return {"name": entry.name, "type": entry.entry_type, "path": entry.path}


def _match_to_payload(match: MatchInfo) -> Dict[str, Any]:
    return {"name": match.name, "path": match.path, "size": match.size}


def _search_match_to_payload(match: SearchMatch) -> Dict[str, Any]:
    return {"path": match.path, "line": match.line, "snippet": match.snippet}


def _tool_error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return error_dict_from_mapping(
        {"code": code, "message": message, "details": details},
        include_details=details is not None,
        include_empty_details=bool(details),
    )


def _tool_error_result(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    **payload: Any,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": _tool_error_payload(code=code, message=message, details=details),
        **payload,
    }


def _tool_error_result_from_exception(
    exc: BaseException,
    *,
    default_code: str = "INTERNAL_ERROR",
    **payload: Any,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": error_dict_from_exception(
            exc,
            default_code=default_code,
            include_details=True,
            include_empty_details=False,
        ),
        **payload,
    }


def _h_list_dir(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileListDirArgs.model_validate(args)
    raw_path = validated.path or "."
    recursive = validated.recursive
    max_entries = validated.max_entries
    include_hidden = validated.include_hidden

    try:
        resolved = _resolve_path_lexical(ctx, raw_path, operation="read")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e, entries=[])

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {raw_path}",
            entries=[],
        )
    if not backend.is_dir(resolved):
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a directory",
            entries=[],
        )

    try:
        result = backend.list_dir(
            resolved,
            recursive=recursive,
            max_entries=max_entries,
            include_hidden=include_hidden,
        )
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e, entries=[])

    return {
        "ok": True,
        "path": resolved,
        "entries": [_entry_to_payload(entry) for entry in result.entries],
        "count": result.count,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_read_file(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileReadArgs.model_validate(args)

    max_chars = validated.max_chars
    offset = validated.offset

    try:
        resolved = _resolve_path_lexical(ctx, validated.path, operation="read")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
        )
    if not backend.is_file(resolved):
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a file",
        )
    try:
        result = backend.read(resolved, max_chars=max_chars, offset=offset)
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)
    # PIDF: route file_read through the typed boundary owner.
    _pidf_emit_boundary_event(
        "file_read",
        result.content,
        seam_id="tools.file.plugin.read_file",
        provenance_ref=str(resolved),
    )
    return {
        "ok": True,
        "path": resolved,
        "content": result.content,
        "truncated": result.truncated,
        "total_length": result.total_length,
        "returned_length": result.returned_length,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_read_range(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileReadRangeArgs.model_validate(args)

    try:
        resolved = _resolve_path_lexical(ctx, validated.path, operation="read")
    except ToolRuntimeError as exc:
        return _tool_error_result_from_exception(exc)

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
        )
    if not backend.is_file(resolved):
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a file",
        )
    try:
        result = backend.read(
            resolved,
            max_chars=FILE_MAX_READ_CHARS,
            offset=0,
        )
    except ToolRuntimeError as exc:
        return _tool_error_result_from_exception(exc)

    lines = result.content.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return {
            "ok": True,
            "path": resolved,
            "start_line": 1,
            "end_line": 0,
            "total_lines": 0,
            "content": "",
            "truncated": result.truncated,
            "source": _FILE_TOOL_SOURCE,
        }

    start_line = min(validated.start_line, total_lines)
    end_line = min(validated.end_line, total_lines)
    selected = lines[start_line - 1 : end_line]
    numbered = "\n".join(
        f"{line_no}: {line}" for line_no, line in enumerate(selected, start=start_line)
    )
    return {
        "ok": True,
        "path": resolved,
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total_lines,
        "content": numbered,
        "truncated": result.truncated,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_write_file(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileWriteArgs.model_validate(args)

    content = validated.content or ""
    if len(content) > FILE_MAX_WRITE_CHARS:
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message=f"content exceeds maximum {FILE_MAX_WRITE_CHARS} characters",
        )

    append = validated.append
    create_dirs = validated.create_dirs

    try:
        resolved = _resolve_path_lexical(ctx, validated.path, operation="write")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)

    backend = _get_backend(ctx)
    try:
        result = backend.write(
            resolved,
            content,
            append=append,
            create_dirs=create_dirs,
        )
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)

    from openminion.tools.code.cache import invalidate_repo_map_cache

    invalidate_repo_map_cache(
        workspace_root=_resolve_workspace_root(ctx),
        path=result.path,
    )
    return {
        "ok": True,
        "path": result.path,
        "bytes_written": result.bytes_written,
        "mode": result.mode,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_find_files(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileFindArgs.model_validate(args)

    raw_path = validated.path or "."
    pattern = validated.pattern
    max_entries = validated.max_entries
    include_hidden = validated.include_hidden

    try:
        resolved = _resolve_path_lexical(ctx, raw_path, operation="read")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e, matches=[])

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {raw_path}",
            matches=[],
        )
    if not backend.is_dir(resolved):
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a directory",
            matches=[],
        )
    try:
        result = backend.find(
            resolved,
            pattern=pattern,
            max_entries=max_entries,
            include_hidden=include_hidden,
        )
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e, matches=[])
    return {
        "ok": True,
        "path": resolved,
        "pattern": pattern,
        "matches": [_match_to_payload(match) for match in result.matches],
        "count": result.count,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_trash(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileTrashArgs.model_validate(args)

    try:
        resolved = _resolve_path_lexical(ctx, validated.path, operation="write")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
        )

    try:
        trashed = backend.trash(resolved)
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)
    return {
        "ok": True,
        "path": resolved,
        "trashed": trashed,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_search_files(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileSearchArgs.model_validate(args)
    raw_path = validated.path or "."
    query = validated.query
    use_regex = validated.regex
    case_sensitive = validated.case_sensitive
    context_lines = validated.context_lines
    max_matches = validated.max_matches
    include_hidden = validated.include_hidden
    file_glob = validated.file_glob

    try:
        resolved = _resolve_path_lexical(ctx, raw_path, operation="read")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e, matches=[])

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {raw_path}",
            matches=[],
        )
    if not backend.is_dir(resolved):
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a directory",
            matches=[],
        )

    def _path_filter(candidate_path: str) -> bool:
        try:
            _resolve_path_lexical(ctx, candidate_path, operation="read")
        except ToolRuntimeError as exc:
            if exc.code == "POLICY_DENIED":
                return False
            raise
        return True

    try:
        result = backend.search(
            resolved,
            query=query,
            regex=use_regex,
            case_sensitive=case_sensitive,
            context_lines=context_lines,
            max_matches=max_matches,
            include_hidden=include_hidden,
            file_glob=file_glob,
            path_filter=_path_filter,
        )
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e, matches=[])

    return {
        "ok": True,
        "path": resolved,
        "query": query,
        "matches": [_search_match_to_payload(match) for match in result.matches],
        "count": result.count,
        "scanned_files": result.scanned_files,
        "source": _FILE_TOOL_SOURCE,
    }


def _h_edit_file(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = FileEditArgs.model_validate(args)
    dry_run = validated.dry_run

    try:
        resolved = _resolve_path_lexical(ctx, validated.path, operation="write")
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)

    backend = _get_backend(ctx)
    if not backend.exists(resolved):
        return _tool_error_result(
            code="NOT_FOUND",
            message=f"file does not exist: {validated.path}",
        )
    if not backend.is_file(resolved):
        return _tool_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a file",
        )

    operations = [
        EditOperation(op=op.op, old_text=op.old_text, new_text=op.new_text)
        for op in validated.operations
    ]
    try:
        result = backend.edit(resolved, operations, dry_run=dry_run)
    except ToolRuntimeError as e:
        return _tool_error_result_from_exception(e)

    if result.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "preview": result.preview,
            "source": _FILE_TOOL_SOURCE,
        }

    from openminion.tools.code.cache import invalidate_repo_map_cache

    invalidate_repo_map_cache(
        workspace_root=_resolve_workspace_root(ctx),
        path=result.path,
    )

    return {
        "ok": True,
        "path": result.path,
        "operations_applied": result.operations_applied,
        "source": _FILE_TOOL_SOURCE,
    }


def register(registry: ToolRegistry) -> None:
    """Register file tools with runtime registry."""
    registry.add(
        ToolSpec(
            name=MODEL_FILE_LIST_DIR,
            args_model=FileListDirArgs,
            min_scope="READ_ONLY",
            handler=_h_list_dir,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_READ,
            args_model=FileReadArgs,
            min_scope="READ_ONLY",
            handler=_h_read_file,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_READ_RANGE,
            args_model=FileReadRangeArgs,
            min_scope="READ_ONLY",
            handler=_h_read_range,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_WRITE,
            args_model=FileWriteArgs,
            min_scope="WRITE_SAFE",
            handler=_h_write_file,
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_FIND,
            args_model=FileFindArgs,
            min_scope="READ_ONLY",
            handler=_h_find_files,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_TRASH,
            args_model=FileTrashArgs,
            min_scope="WRITE_SAFE",
            handler=_h_trash,
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_SEARCH,
            args_model=FileSearchArgs,
            min_scope="READ_ONLY",
            handler=_h_search_files,
            idempotent=True,
        )
    )

    registry.add(
        ToolSpec(
            name=MODEL_FILE_EDIT,
            args_model=FileEditArgs,
            min_scope="WRITE_SAFE",
            handler=_h_edit_file,
            dangerous=False,
            idempotent=False,
            block_under_readonly=True,
        )
    )
