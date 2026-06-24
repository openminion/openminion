from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


IDENTITY_LOCKFILE_NAME = ".identity-lock.json"


@dataclass(frozen=True)
class IdentityLockManifestEntry:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class IdentityLockfile:
    generated_from_profile_version: str
    generated_at: str
    files: tuple[IdentityLockManifestEntry, ...]
    tree_sha256: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "generated_from_profile_version": str(self.generated_from_profile_version),
            "generated_at": str(self.generated_at),
            "files": [
                {
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "size_bytes": int(item.size_bytes),
                }
                for item in self.files
            ],
        }
        if self.tree_sha256:
            payload["tree_sha256"] = str(self.tree_sha256)
        return payload


def build_lock_manifest(
    bundle_root: str | Path,
    *,
    include_tree_hash: bool = True,
) -> tuple[IdentityLockManifestEntry, ...]:
    root = Path(bundle_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"bundle root not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"bundle root is not a directory: {root}")

    entries: list[IdentityLockManifestEntry] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == IDENTITY_LOCKFILE_NAME:
            continue
        data = path.read_bytes()
        entries.append(
            IdentityLockManifestEntry(
                relative_path=rel,
                sha256=sha256(data).hexdigest(),
                size_bytes=len(data),
            )
        )

    return tuple(sorted(entries, key=lambda item: item.relative_path))


def compute_tree_sha256(entries: tuple[IdentityLockManifestEntry, ...]) -> str:
    parts = [f"{item.relative_path}:{item.sha256}" for item in entries]
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def write_identity_lockfile(
    lockfile_path: str | Path, lockfile: IdentityLockfile
) -> None:
    path = Path(lockfile_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = lockfile.to_payload()
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def read_identity_lockfile(lockfile_path: str | Path) -> IdentityLockfile:
    path = Path(lockfile_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files", [])
    entries = tuple(
        IdentityLockManifestEntry(
            relative_path=str(item.get("relative_path", "")),
            sha256=str(item.get("sha256", "")),
            size_bytes=int(item.get("size_bytes", 0)),
        )
        for item in files
    )
    return IdentityLockfile(
        generated_from_profile_version=str(
            payload.get("generated_from_profile_version", "")
        ),
        generated_at=str(payload.get("generated_at", "")),
        files=tuple(sorted(entries, key=lambda item: item.relative_path)),
        tree_sha256=(
            str(payload.get("tree_sha256"))
            if payload.get("tree_sha256") is not None
            else None
        ),
    )
