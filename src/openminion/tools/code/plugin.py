import ast
import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from openminion.base.errors.adapt import (
    error_dict_from_exception,
    error_dict_from_mapping,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_GREP,
    MODEL_CODE_PATCH,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_SYMBOL_FIND,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.config import workspace_retry_path

from .cache import invalidate_repo_map_cache, repo_map_cache_get, repo_map_cache_put
from .interfaces import RepoIndex, RepoIndexFile, RepoIndexImport, RepoIndexSymbol

_REPO_MAP_SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)


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


class CodePatchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("path", "file_path", "filename", "target"),
    )
    patch: str = Field(
        ..., min_length=1, validation_alias=AliasChoices("patch", "diff")
    )

    @model_validator(mode="before")
    @classmethod
    def _collapse_compatible_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _collapse_alias_group(data, "path", ("file_path", "filename", "target"))
        _collapse_alias_group(data, "patch", ("diff",))
        data.pop("workspace", None)
        return data

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("path is required")
        return normalized


class CodeGrepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., min_length=1)
    path: str = Field(default=".")
    file_glob: str = Field(default="*")
    max_results: int = Field(default=200, ge=1, le=1000)

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."

    @field_validator("file_glob", mode="before")
    @classmethod
    def _normalize_file_glob(cls, value: Any) -> str:
        return str(value or "*").strip() or "*"


class CodeRepoMapArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".")
    max_tokens: int = Field(default=4096, ge=128, le=32768)
    include_hidden: bool = Field(default=False)

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."


class CodeRepoIndexArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".")
    max_files: int = Field(default=200, ge=1, le=5000)

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."


class CodeSymbolFindArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1)
    path: str = Field(default=".")
    max_results: int = Field(default=20, ge=1, le=100)

    @field_validator("symbol", mode="before")
    @classmethod
    def _normalize_symbol(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("symbol is required")
        return normalized

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str:
        return str(value or ".").strip() or "."


def _workspace_root_from_context(ctx: RuntimeContext) -> Path:
    raw = getattr(ctx.policy, "raw", {})
    workspace_root = raw.get("workspace_root") if isinstance(raw, dict) else None
    if workspace_root:
        return Path(workspace_root).expanduser().resolve(strict=False)
    return Path(ctx.workspace).expanduser().resolve(strict=False)


def _relative_base_dir_from_context(ctx: RuntimeContext) -> Path:
    workspace_root = _workspace_root_from_context(ctx)
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


def _resolve_code_path(ctx: RuntimeContext, raw_path: str, operation: str) -> Path:
    workspace_root = _workspace_root_from_context(ctx)
    relative_base_dir = _relative_base_dir_from_context(ctx)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = relative_base_dir / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        retry_path = workspace_retry_path(raw_path)
        raise ToolRuntimeError(
            "POLICY_DENIED",
            (
                f"path escapes workspace root: {raw_path}. "
                f"Use a relative path under the workspace root, for example {retry_path}."
            ),
            details={
                "workspace_root": str(workspace_root),
                "retry_path": retry_path,
                "retry_hint": "Use a relative path under the workspace root.",
            },
        ) from exc
    ctx.policy.ensure_path_allowed(
        str(resolved),
        workspace=workspace_root,
        operation=operation,
    )
    return resolved


def _code_error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return error_dict_from_mapping(
        {"code": code, "message": message, "details": details},
        include_details=details is not None,
        include_empty_details=bool(details),
    )


def _code_error_result(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": _code_error_payload(code=code, message=message, details=details),
        **payload,
    }


def _code_error_result_from_exception(
    exc: BaseException,
    *,
    default_code: str = "INTERNAL_ERROR",
    **payload: Any,
) -> dict[str, Any]:
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


def _line_number_range(path: Path) -> tuple[int, int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 1, 1
    return 1, max(1, len(lines))


def _definition_pattern(symbol: str) -> re.Pattern[str]:
    escaped = re.escape(symbol)
    return re.compile(rf"^\s*(?:class|def|async\s+def)\s+{escaped}\b")


def _h_patch(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = CodePatchArgs.model_validate(args)
    try:
        resolved = _resolve_code_path(ctx, validated.path, "write")
    except ToolRuntimeError as exc:
        return _code_error_result_from_exception(exc)
    if not resolved.exists():
        return _code_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
        )
    if not resolved.is_file():
        return _code_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a file",
        )

    workspace_root = _workspace_root_from_context(ctx)
    completed = subprocess.run(
        ["patch", "--forward", "--silent", str(resolved)],
        input=validated.patch,
        text=True,
        capture_output=True,
        cwd=str(workspace_root),
        check=False,
    )
    if completed.returncode != 0:
        details = {
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip(),
            "stdout": completed.stdout.strip(),
        }
        return _code_error_result(
            code="PATCH_FAILED",
            message="patch did not apply",
            details=details,
        )

    hunk_count = max(1, validated.patch.count("\n@@ "))
    invalidate_repo_map_cache(workspace_root=workspace_root, path=str(resolved))
    return {
        "ok": True,
        "path": str(resolved),
        "hunk_count": hunk_count,
    }


def _h_grep(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = CodeGrepArgs.model_validate(args)
    try:
        resolved = _resolve_code_path(ctx, validated.path, "read")
    except ToolRuntimeError as exc:
        return _code_error_result_from_exception(exc, matches=[])
    if not resolved.exists():
        return _code_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
            matches=[],
        )

    command = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--glob",
        validated.file_glob,
        validated.pattern,
        str(resolved),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_workspace_root_from_context(ctx)),
        )
    except FileNotFoundError as exc:
        return _code_error_result_from_exception(
            exc, default_code="EXEC_ERROR", matches=[]
        )

    if completed.returncode not in (0, 1):
        return _code_error_result(
            code="EXEC_ERROR",
            message=(completed.stderr.strip() or "rg search failed"),
            details={"returncode": completed.returncode},
            matches=[],
        )

    matches: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if len(matches) >= validated.max_results:
            break
        try:
            file_path, line_no, text = line.split(":", 2)
            matches.append(
                {
                    "file": file_path,
                    "line": int(line_no),
                    "text": text,
                }
            )
        except ValueError:
            continue
    return {
        "ok": True,
        "path": str(resolved),
        "pattern": validated.pattern,
        "matches": matches,
        "count": len(matches),
    }


def _collect_python_symbols(path: Path, *, limit: int = 8) -> list[str]:
    if path.suffix != ".py" or not path.is_file():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(node.name)
        if len(out) >= limit:
            break
    return out


def _parse_python_index(
    path: Path,
) -> tuple[list[RepoIndexSymbol], list[RepoIndexImport]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    symbols: list[RepoIndexSymbol] = []
    imports: list[RepoIndexImport] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                RepoIndexSymbol(
                    name=node.name,
                    kind="class" if isinstance(node, ast.ClassDef) else "function",
                    file=str(path),
                    start_line=int(getattr(node, "lineno", 1) or 1),
                    end_line=int(
                        getattr(node, "end_lineno", None)
                        or getattr(node, "lineno", 1)
                        or 1
                    ),
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    RepoIndexImport(
                        importer=str(path),
                        module=str(alias.name or "").strip(),
                        imported_names=[],
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                RepoIndexImport(
                    importer=str(path),
                    module=str(node.module or "").strip() or ".",
                    imported_names=[
                        str(alias.name or "").strip()
                        for alias in node.names
                        if str(alias.name or "").strip()
                    ],
                )
            )
    return symbols, imports


def _render_repo_map(
    *,
    root: Path,
    max_tokens: int,
    include_hidden: bool,
) -> str:
    budget_chars = max(1, int(max_tokens)) * 4
    lines: list[str] = [f"{root.name or str(root)}"]
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not include_hidden and any(part.startswith(".") for part in relative.parts):
            continue
        if any(part in _REPO_MAP_SKIP_DIRS for part in relative.parts):
            continue
        indent = "  " * max(0, len(relative.parts) - 1)
        suffix = "/" if path.is_dir() else ""
        line = f"{indent}{relative.name}{suffix}"
        symbols = _collect_python_symbols(path)
        if symbols:
            line += " :: " + ", ".join(symbols)
        lines.append(line)
        payload = "\n".join(lines)
        if len(payload) >= budget_chars:
            return payload[:budget_chars]
    return "\n".join(lines)


def _h_repo_map(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = CodeRepoMapArgs.model_validate(args)
    try:
        resolved = _resolve_code_path(ctx, validated.path, "read")
    except ToolRuntimeError as exc:
        return _code_error_result_from_exception(exc)
    if not resolved.exists():
        return _code_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
        )
    if not resolved.is_dir():
        return _code_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a directory",
        )

    session_id = str(getattr(ctx, "telemetry_session_id", "") or "").strip()
    cached = repo_map_cache_get(
        session_id=session_id,
        workspace_root=resolved,
        include_hidden=validated.include_hidden,
        max_tokens=validated.max_tokens,
    )
    if cached is not None:
        return {
            "ok": True,
            "path": str(resolved),
            "repo_map": cached,
            "cached": True,
        }

    repo_map = _render_repo_map(
        root=resolved,
        max_tokens=validated.max_tokens,
        include_hidden=validated.include_hidden,
    )
    repo_map_cache_put(
        session_id=session_id,
        workspace_root=resolved,
        include_hidden=validated.include_hidden,
        max_tokens=validated.max_tokens,
        payload=repo_map,
    )
    return {
        "ok": True,
        "path": str(resolved),
        "repo_map": repo_map,
        "cached": False,
    }


def _h_repo_index(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = CodeRepoIndexArgs.model_validate(args)
    try:
        resolved = _resolve_code_path(ctx, validated.path, "read")
    except ToolRuntimeError as exc:
        return _code_error_result_from_exception(exc)
    if not resolved.exists():
        return _code_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
        )
    if not resolved.is_dir():
        return _code_error_result(
            code="INVALID_ARGUMENT",
            message="path is not a directory",
        )

    files: list[RepoIndexFile] = []
    symbols: list[RepoIndexSymbol] = []
    imports: list[RepoIndexImport] = []
    for path in sorted(resolved.rglob("*")):
        if len(files) >= validated.max_files:
            break
        if not path.is_file():
            continue
        relative = path.relative_to(resolved)
        if any(part in _REPO_MAP_SKIP_DIRS for part in relative.parts):
            continue
        top_level_symbols = _collect_python_symbols(path)
        file_imports: list[str] = []
        if path.suffix == ".py":
            parsed_symbols, parsed_imports = _parse_python_index(path)
            symbols.extend(parsed_symbols)
            imports.extend(parsed_imports)
            file_imports = [
                item.module for item in parsed_imports if item.importer == str(path)
            ]
        files.append(
            RepoIndexFile(
                path=str(path),
                language="python" if path.suffix == ".py" else "unknown",
                top_level_symbols=top_level_symbols,
                imports=file_imports,
            )
        )

    repo_index = RepoIndex(
        root=str(resolved),
        files=files,
        symbols=symbols,
        imports=imports,
    )
    return {
        "ok": True,
        "path": str(resolved),
        "repo_index": repo_index.model_dump(mode="json"),
    }


def _collect_symbol_matches(
    root: Path, symbol: str, max_results: int
) -> list[dict[str, Any]]:
    pattern = _definition_pattern(symbol)
    matches: list[dict[str, Any]] = []
    try:
        completed = subprocess.run(
            [
                "rg",
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--type",
                "py",
                pattern.pattern,
                str(root),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(root),
        )
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode in (0, 1):
        for line in completed.stdout.splitlines():
            if len(matches) >= max_results:
                break
            try:
                file_path, line_no, text = line.split(":", 2)
                start_line = int(line_no)
            except ValueError:
                continue
            kind = "class" if text.lstrip().startswith("class ") else "function"
            end_line = _line_number_range(Path(file_path))[1]
            matches.append(
                {
                    "file": file_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "kind": kind,
                }
            )
        if matches:
            return matches[:max_results]

    for path in sorted(root.rglob("*.py")):
        if len(matches) >= max_results:
            break
        if any(part in _REPO_MAP_SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if len(matches) >= max_results:
                break
            if not isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if node.name != symbol:
                continue
            matches.append(
                {
                    "file": str(path),
                    "start_line": int(getattr(node, "lineno", 1) or 1),
                    "end_line": int(
                        getattr(node, "end_lineno", None)
                        or getattr(node, "lineno", 1)
                        or 1
                    ),
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                }
            )
    return matches[:max_results]


def _h_symbol_find(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = CodeSymbolFindArgs.model_validate(args)
    try:
        resolved = _resolve_code_path(ctx, validated.path, "read")
    except ToolRuntimeError as exc:
        return _code_error_result_from_exception(exc, matches=[])
    if not resolved.exists():
        return _code_error_result(
            code="NOT_FOUND",
            message=f"path does not exist: {validated.path}",
            matches=[],
        )
    matches = _collect_symbol_matches(
        resolved,
        validated.symbol,
        validated.max_results,
    )
    if not matches:
        return _code_error_result(
            code="NOT_FOUND",
            message=f"symbol not found: {validated.symbol}",
            matches=[],
        )
    return {
        "ok": True,
        "symbol": validated.symbol,
        "path": str(resolved),
        "matches": matches,
        "count": len(matches),
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=MODEL_CODE_PATCH,
            args_model=CodePatchArgs,
            min_scope="WRITE_SAFE",
            handler=_h_patch,
            dangerous=False,
            idempotent=False,
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_CODE_GREP,
            args_model=CodeGrepArgs,
            min_scope="READ_ONLY",
            handler=_h_grep,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_CODE_REPO_MAP,
            args_model=CodeRepoMapArgs,
            min_scope="READ_ONLY",
            handler=_h_repo_map,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_CODE_REPO_INDEX,
            args_model=CodeRepoIndexArgs,
            min_scope="READ_ONLY",
            handler=_h_repo_index,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_CODE_SYMBOL_FIND,
            args_model=CodeSymbolFindArgs,
            min_scope="READ_ONLY",
            handler=_h_symbol_find,
        )
    )


__all__ = [
    "CodeGrepArgs",
    "CodePatchArgs",
    "CodeRepoIndexArgs",
    "CodeRepoMapArgs",
    "CodeSymbolFindArgs",
    "register",
]
