from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from collections.abc import Sequence

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config


@dataclass(frozen=True)
class IdentityDocument:
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": int(self.size_bytes),
        }


@dataclass(frozen=True)
class IdentityBundle:
    agent_id: str
    root_path: str
    fingerprint: str
    agent: IdentityDocument | None = None
    soul: IdentityDocument | None = None
    skills: Sequence[IdentityDocument] = field(default_factory=tuple)
    notes: Sequence[IdentityDocument] = field(default_factory=tuple)
    errors: Sequence[str] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "agent_id": self.agent_id,
            "root_path": self.root_path,
            "fingerprint": self.fingerprint,
            "agent": self.agent.to_dict() if self.agent is not None else None,
            "soul": self.soul.to_dict() if self.soul is not None else None,
            "skills": [document.to_dict() for document in self.skills],
            "notes": [document.to_dict() for document in self.notes],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def load_identity_bundle(
    agent_id: str, *, root: str | Path | None = None
) -> IdentityBundle:
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise RuntimeError("`agent_id` is required.")

    root_path = _resolve_identity_root(root)
    bundle_root = _resolve_bundle_root(root_path, normalized_agent_id)
    errors: list[str] = []
    warnings: list[str] = []
    documents: list[IdentityDocument] = []

    if not bundle_root.is_dir():
        reason = "not found" if not bundle_root.exists() else "is not a directory"
        errors.append(f"identity bundle root {reason}: {bundle_root}")
        return _build_bundle(
            agent_id=normalized_agent_id,
            bundle_root=bundle_root,
            agent_document=None,
            soul_document=None,
            skills=(),
            notes=(),
            errors=tuple(errors),
            warnings=tuple(warnings),
            documents=(),
        )

    agent_document = _load_markdown_document(
        path=bundle_root / "AGENT.md",
        root=bundle_root,
        required_label="AGENT.md",
        errors=errors,
    )
    if agent_document is not None:
        documents.append(agent_document)
    soul_document = _load_markdown_document(
        path=bundle_root / "SOUL.md",
        root=bundle_root,
        required_label="SOUL.md",
        errors=errors,
    )
    if soul_document is not None:
        documents.append(soul_document)

    skills_root = bundle_root / "SKILLS"
    skills = _load_tree_documents(skills_root, "SKILL.md", bundle_root)
    if not skills:
        warnings.append("no skill files found under SKILLS/**/SKILL.md")
    documents.extend(skills)

    notes_root = bundle_root / "NOTES"
    notes = _load_tree_documents(notes_root, "*.md", bundle_root)
    documents.extend(notes)

    return _build_bundle(
        agent_id=normalized_agent_id,
        bundle_root=bundle_root,
        agent_document=agent_document,
        soul_document=soul_document,
        skills=tuple(skills),
        notes=tuple(notes),
        errors=tuple(errors),
        warnings=tuple(warnings),
        documents=tuple(documents),
    )


def _resolve_identity_root(root: str | Path | None) -> Path:
    data_root = _default_data_root()
    if root:
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            candidate = data_root / candidate
        return candidate.resolve(strict=False)

    return data_root


def _default_data_root() -> Path:
    env = resolve_environment_config()
    env_map = env.snapshot()
    home_root = resolve_home_root(
        config_path=None,
        fallback=str(Path.cwd()),
        env=env_map,
    ).resolve(strict=False)
    return resolve_data_root(
        home_root,
        data_root=str(env.openminion_data_root or "").strip() or None,
        env=env_map,
    ).resolve(strict=False)


def _resolve_bundle_root(root_path: Path, agent_id: str) -> Path:
    if root_path.name == agent_id and root_path.parent.name == "agents":
        return root_path
    if root_path.name == "agents":
        return root_path / agent_id
    return root_path / "agents" / agent_id


def _build_bundle(
    *,
    agent_id: str,
    bundle_root: Path,
    agent_document: IdentityDocument | None,
    soul_document: IdentityDocument | None,
    skills: Sequence[IdentityDocument],
    notes: Sequence[IdentityDocument],
    errors: Sequence[str],
    warnings: Sequence[str],
    documents: Sequence[IdentityDocument],
) -> IdentityBundle:
    fingerprint_entries = [
        f"{document.relative_path}:{document.sha256}"
        for document in sorted(documents, key=lambda item: item.relative_path)
    ]
    fingerprint = sha256("\n".join(fingerprint_entries).encode("utf-8")).hexdigest()
    return IdentityBundle(
        agent_id=agent_id,
        root_path=str(bundle_root),
        fingerprint=fingerprint,
        agent=agent_document,
        soul=soul_document,
        skills=tuple(skills),
        notes=tuple(notes),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _case_insensitive_child(root: Path, name: str) -> Path:
    candidate = root / name
    normalized_name = name.lower()
    try:
        for child in root.iterdir():
            if child.name.lower() == normalized_name:
                return child
    except OSError:
        return candidate
    return candidate


def _load_markdown_document(
    *,
    path: Path,
    root: Path,
    required_label: str,
    errors: list[str],
) -> IdentityDocument | None:
    resolved_path = _case_insensitive_child(path.parent, path.name)
    if not resolved_path.exists():
        errors.append(f"missing required identity file: {required_label}")
        return None
    if not resolved_path.is_file():
        errors.append(f"required identity file is not a regular file: {required_label}")
        return None
    return _read_document(path=resolved_path, root=root)


def _load_tree_documents(
    root: Path, pattern: str, bundle_root: Path
) -> list[IdentityDocument]:
    resolved_root = _case_insensitive_child(root.parent, root.name)
    if not resolved_root.exists() or not resolved_root.is_dir():
        return []
    documents: list[IdentityDocument] = []
    normalized_pattern = pattern.lower()
    for path in sorted(resolved_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() != normalized_pattern and normalized_pattern != "*.md":
            continue
        if normalized_pattern == "*.md" and path.suffix.lower() != ".md":
            continue
        documents.append(_read_document(path=path, root=bundle_root))
    return documents


def _read_document(*, path: Path, root: Path) -> IdentityDocument:
    payload = path.read_bytes()
    relative_path = path.relative_to(root).as_posix()
    return IdentityDocument(
        relative_path=relative_path,
        sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
