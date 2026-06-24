from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from openminion.modules.identity.runtime.lockfile import (
    IDENTITY_LOCKFILE_NAME,
    IdentityLockManifestEntry,
    IdentityLockfile,
    compute_tree_sha256,
    write_identity_lockfile,
)
from openminion.modules.identity.runtime.md_generator import (
    export_profile_to_markdown_bundle,
)
from openminion.modules.identity.models import AgentProfile


GENERATED_IDENTITY_README_NAME = "README.md"


def materialize_generated_identity_bundle(
    *,
    profile: AgentProfile,
    bundle_root: str | Path,
    profile_version: str,
) -> None:
    profile_obj = AgentProfile.model_validate(profile)
    root = Path(bundle_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    export_result = export_profile_to_markdown_bundle(profile_obj)
    tracked_paths: list[str] = []
    for document in export_result.documents:
        destination = root / document.relative_path
        _write_text_if_changed(destination, document.content)
        tracked_paths.append(document.relative_path)

    readme_path = root / GENERATED_IDENTITY_README_NAME
    _write_text_if_changed(
        readme_path,
        render_generated_identity_readme(agent_id=profile_obj.agent_id),
    )
    tracked_paths.append(GENERATED_IDENTITY_README_NAME)

    entries = _build_manifest_for_paths(root=root, relative_paths=tracked_paths)
    lockfile = IdentityLockfile(
        generated_from_profile_version=str(profile_version),
        generated_at=datetime.now(timezone.utc).isoformat(),
        files=entries,
        tree_sha256=compute_tree_sha256(entries),
    )
    write_identity_lockfile(root / IDENTITY_LOCKFILE_NAME, lockfile)


def render_generated_identity_readme(*, agent_id: str) -> str:
    return (
        "# Generated Identity Files\n\n"
        f"This directory contains generated markdown sidecars for `{agent_id}`.\n\n"
        "- `profile.yaml` is the authoring source of truth.\n"
        "- Runtime identity is loaded from YAML into SQLite.\n"
        "- `AGENT.md` and `SOUL.md` are auto-generated for readability and compatibility.\n\n"
        "Editing these generated markdown files does not change the runtime profile.\n"
        "Update `profile.yaml` instead; generated files may be overwritten on the next "
        "identity sync or `identity upsert`.\n"
    )


def resolve_generated_bundle_root_for_profile_path(
    source_path: str | Path,
) -> Path | None:
    candidate = Path(source_path).expanduser().resolve(strict=False)
    if candidate.name != "profile.yaml":
        return None
    return candidate.parent


def _write_text_if_changed(path: Path, content: str) -> None:
    normalized = content if content.endswith("\n") else f"{content}\n"
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing == normalized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")


def _build_manifest_for_paths(
    *,
    root: Path,
    relative_paths: list[str],
) -> tuple[IdentityLockManifestEntry, ...]:
    entries: list[IdentityLockManifestEntry] = []
    for relative_path in sorted(set(relative_paths)):
        payload = (root / relative_path).read_bytes()
        entries.append(
            IdentityLockManifestEntry(
                relative_path=relative_path,
                sha256=sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )
    return tuple(entries)
