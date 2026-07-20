from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROJECT_INSTRUCTION_FILENAMES: tuple[str, ...] = (
    "OPENMINION.md",
    "AGENTS.md",
    "CLAUDE.md",
)
DEFAULT_PROJECT_INSTRUCTION_FILENAME = "OPENMINION.md"
PROJECT_INSTRUCTION_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProjectInstructionTarget:
    project_root: Path
    path: Path
    target_name: str
    exists: bool
    size_bytes: int
    content_hash: str
    newline: str
    encoding: str
    mode: int | None
    content: str = ""
    truncated: bool = False

    @property
    def display_path(self) -> str:
        try:
            return str(self.path.relative_to(self.project_root))
        except ValueError:
            return str(self.path)


def resolve_project_instruction_target(
    working_dir: str | Path | None,
    *,
    target_name: str | None = None,
    max_bytes: int = PROJECT_INSTRUCTION_MAX_BYTES,
) -> ProjectInstructionTarget:
    start = Path(working_dir or ".").expanduser().resolve(strict=False)
    probe = start if start.is_dir() else start.parent
    project_root = _project_root_for(probe)
    name = _normalize_target_name(target_name)
    if name is not None:
        return _target_for(project_root, project_root / name, max_bytes=max_bytes)

    for current in _search_roots(probe, project_root):
        for filename in PROJECT_INSTRUCTION_FILENAMES:
            candidate = current / filename
            if candidate.is_file():
                found_root = _root_for_found_file(candidate, project_root)
                return _target_for(found_root, candidate, max_bytes=max_bytes)
    return _target_for(
        project_root,
        project_root / DEFAULT_PROJECT_INSTRUCTION_FILENAME,
        max_bytes=max_bytes,
    )


def compute_instruction_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_instruction_target_snapshot(
    path: str | Path,
    *,
    project_root: str | Path | None = None,
) -> ProjectInstructionTarget:
    raw_path = Path(path).expanduser()
    root = (
        Path(project_root).expanduser().resolve(strict=False)
        if project_root is not None
        else _project_root_for(raw_path.resolve(strict=False).parent)
    )
    return _target_for(root, raw_path)


def _project_root_for(probe: Path) -> Path:
    for current in (probe, *probe.parents):
        if (current / ".git").exists():
            return current.resolve(strict=False)
    return probe.resolve(strict=False)


def _search_roots(probe: Path, project_root: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    for current in (probe, *probe.parents):
        roots.append(current)
        if project_root != probe and current == project_root:
            break
    return tuple(roots)


def _root_for_found_file(path: Path, project_root: Path) -> Path:
    if project_root != project_root.parent and project_root in path.parents:
        return project_root
    return path.parent.resolve(strict=False)


def _target_for(
    project_root: Path,
    path: Path,
    *,
    max_bytes: int = PROJECT_INSTRUCTION_MAX_BYTES,
) -> ProjectInstructionTarget:
    raw_path = path.expanduser()
    if raw_path.exists() and raw_path.is_symlink():
        raise ValueError(f"project_instruction_target_symlink:{raw_path}")
    resolved_path = raw_path.resolve(strict=False)
    resolved_root = project_root.expanduser().resolve(strict=False)
    _assert_allowed_target(resolved_path, resolved_root)
    if not resolved_path.exists():
        return ProjectInstructionTarget(
            project_root=resolved_root,
            path=resolved_path,
            target_name=resolved_path.name,
            exists=False,
            size_bytes=0,
            content_hash=compute_instruction_content_hash(""),
            newline="\n",
            encoding="utf-8",
            mode=None,
        )
    if not resolved_path.is_file():
        raise ValueError(f"project_instruction_target_not_file:{resolved_path}")
    raw = resolved_path.read_text(encoding="utf-8", errors="replace")
    size_bytes = len(raw.encode("utf-8"))
    content = raw
    truncated = False
    if size_bytes > max_bytes:
        head_budget = max_bytes // 2
        tail_budget = max_bytes - head_budget
        encoded = raw.encode("utf-8", errors="replace")
        head = encoded[:head_budget].decode("utf-8", errors="ignore").rstrip()
        tail = encoded[-tail_budget:].decode("utf-8", errors="ignore").lstrip()
        content = f"{head}\n\n[... project context truncated ...]\n\n{tail}".strip()
        truncated = True
    return ProjectInstructionTarget(
        project_root=resolved_root,
        path=resolved_path,
        target_name=resolved_path.name,
        exists=True,
        size_bytes=size_bytes,
        content_hash=compute_instruction_content_hash(raw),
        newline=_detect_newline(raw),
        encoding="utf-8",
        mode=resolved_path.stat().st_mode & 0o777,
        content=content,
        truncated=truncated,
    )


def _normalize_target_name(target_name: str | None) -> str | None:
    raw = str(target_name or "").strip()
    if not raw:
        return None
    if raw not in PROJECT_INSTRUCTION_FILENAMES:
        raise ValueError(f"unsupported_project_instruction_target:{raw}")
    return raw


def _assert_allowed_target(path: Path, project_root: Path) -> None:
    if path.name not in PROJECT_INSTRUCTION_FILENAMES:
        raise ValueError(f"unsupported_project_instruction_target:{path.name}")
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"project_instruction_target_escape:{path}") from exc
    parent = path.parent
    if parent.exists() and parent.resolve(strict=False) != parent:
        raise ValueError(f"project_instruction_parent_symlink:{parent}")


def _detect_newline(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    if "\r" in content:
        return "\r"
    return "\n"


__all__ = [
    "DEFAULT_PROJECT_INSTRUCTION_FILENAME",
    "PROJECT_INSTRUCTION_FILENAMES",
    "ProjectInstructionTarget",
    "compute_instruction_content_hash",
    "read_instruction_target_snapshot",
    "resolve_project_instruction_target",
]
