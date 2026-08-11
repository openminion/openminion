from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openminion.modules.runtime.project_instructions import (
    PROJECT_INSTRUCTION_MAX_BYTES,
    ProjectInstructionTarget,
    resolve_project_instruction_target,
)

_PROJECT_CONTEXT_MAX_BYTES = PROJECT_INSTRUCTION_MAX_BYTES


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
    target = resolve_project_instruction_target(working_dir, max_bytes=max_bytes)
    if target.exists:
        return _context_info_from_target(target)
    return None


def find_project_context_target_root(working_dir: str | Path | None) -> Path:
    target = resolve_project_instruction_target(working_dir)
    return target.path.parent if target.exists else target.project_root


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


def _context_info_from_target(target: ProjectInstructionTarget) -> ProjectContextInfo:
    return ProjectContextInfo(
        path=target.path,
        source_name=target.target_name,
        size_bytes=target.size_bytes,
        content=target.content,
        truncated=target.truncated,
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
