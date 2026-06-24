from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_PROJECT_CONTEXT_FILENAMES: tuple[str, ...] = (
    "OPENMINION.md",
    "AGENTS.md",
    "CLAUDE.md",
)
_PROJECT_CONTEXT_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProjectContextInfo:
    path: Path
    source_name: str
    size_bytes: int
    content: str
    truncated: bool = False

    @property
    def is_canonical_name(self) -> bool:
        return self.source_name == "OPENMINION.md"

    @property
    def display_name(self) -> str:
        return self.path.name or self.source_name


def resolve_project_context(
    working_dir: str | Path | None,
    *,
    max_bytes: int = _PROJECT_CONTEXT_MAX_BYTES,
) -> ProjectContextInfo | None:
    start = Path(working_dir or ".").expanduser().resolve(strict=False)
    probe = start if start.is_dir() else start.parent
    for current in (probe, *probe.parents):
        for filename in _PROJECT_CONTEXT_FILENAMES:
            candidate = current / filename
            if not candidate.is_file():
                continue
            return _read_project_context(candidate, max_bytes=max_bytes)
    return None


def find_project_context_target_root(working_dir: str | Path | None) -> Path:
    info = resolve_project_context(working_dir)
    if info is not None:
        return info.path.parent
    start = Path(working_dir or ".").expanduser().resolve(strict=False)
    return start if start.is_dir() else start.parent


def build_project_context_metadata(
    info: ProjectContextInfo | None,
) -> dict[str, str]:
    if info is None:
        return {}
    metadata = {
        "project_context_path": str(info.path),
        "project_context_name": info.source_name,
        "project_context_body": info.content,
    }
    if info.truncated:
        metadata["project_context_truncated"] = "true"
    return metadata


def build_init_template(
    *,
    working_dir: str | Path | None,
    agent_id: str,
) -> str:
    project_root = find_project_context_target_root(working_dir)
    project_name = project_root.name or "project"
    readme_summary = _read_readme_summary(project_root)
    architecture_line = readme_summary or (
        "Describe the architecture, important modules, and active surfaces."
    )
    lines = [
        f"# {project_name}",
        "",
        "## Architecture",
        architecture_line,
        "",
        "## Conventions",
        "- Describe code ownership or style rules the agent should preserve.",
        "- Note any commands, validators, or safety rules that matter here.",
        "",
        "## Validation",
        "- List the commands a contributor should run before calling work done.",
        "",
        "## Notes for OpenMinion",
        f"- Default agent: {str(agent_id or 'openminion').strip() or 'openminion'}",
        "- Add anything the shell should know before it starts working here.",
    ]
    return "\n".join(lines).strip() + "\n"


def write_init_template(
    *,
    working_dir: str | Path | None,
    agent_id: str,
) -> Path:
    target_root = find_project_context_target_root(working_dir)
    existing = resolve_project_context(target_root)
    if existing is not None:
        raise FileExistsError(str(existing.path))
    target_path = target_root / "OPENMINION.md"
    target_path.write_text(
        build_init_template(working_dir=target_root, agent_id=agent_id),
        encoding="utf-8",
    )
    return target_path


def _read_project_context(path: Path, *, max_bytes: int) -> ProjectContextInfo:
    raw = path.read_text(encoding="utf-8", errors="replace")
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
    return ProjectContextInfo(
        path=path,
        source_name=path.name,
        size_bytes=size_bytes,
        content=content,
        truncated=truncated,
    )


def _read_readme_summary(project_root: Path) -> str:
    for filename in ("README.md", "README.txt", "README"):
        candidate = project_root / filename
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        chunks = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
        for chunk in chunks:
            raw_lines = [line.strip() for line in chunk.splitlines() if line.strip()]
            if raw_lines and all(line.startswith("#") for line in raw_lines):
                continue
            cleaned = " ".join(
                line.strip().lstrip("#").strip() for line in chunk.splitlines()
            )
            if cleaned:
                return cleaned
    return ""


__all__ = [
    "ProjectContextInfo",
    "build_init_template",
    "build_project_context_metadata",
    "find_project_context_target_root",
    "resolve_project_context",
    "write_init_template",
]
