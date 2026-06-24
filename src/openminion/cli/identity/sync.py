from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.base.config.runtime import resolve_identity_root_from_env
from openminion.base.constants import OPENMINION_IDENTITY_ROOT_ENV
from openminion.cli.config import CLIRoots, resolve_cli_identity_db_path
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage import SQLiteIdentityStore


@dataclass(frozen=True)
class CLIIdentitySyncSummary:
    enabled: bool
    identity_root: str
    profile_files_count: int
    synced_profiles_count: int
    synced_profiles: tuple[str, ...]


def sync_cli_identity_profiles(
    *,
    enabled: bool,
    config: Any,
    roots: CLIRoots,
) -> CLIIdentitySyncSummary:
    if not enabled:
        return CLIIdentitySyncSummary(
            enabled=False,
            identity_root="",
            profile_files_count=0,
            synced_profiles_count=0,
            synced_profiles=(),
        )

    identity_root = _resolve_cli_identity_root(config=config, roots=roots)
    if not identity_root.exists() or not identity_root.is_dir():
        return CLIIdentitySyncSummary(
            enabled=True,
            identity_root=str(identity_root),
            profile_files_count=0,
            synced_profiles_count=0,
            synced_profiles=(),
        )

    profile_paths = _discover_profile_yaml_paths(identity_root)
    if not profile_paths:
        return CLIIdentitySyncSummary(
            enabled=True,
            identity_root=str(identity_root),
            profile_files_count=0,
            synced_profiles_count=0,
            synced_profiles=(),
        )

    db_path = resolve_cli_identity_db_path(config, roots=roots)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
    try:
        synced_profiles: list[str] = []
        for profile_path in profile_paths:
            synced_profiles.extend(
                ctl.load_profiles_from_path(profile_path, skip_unchanged=True)
            )
    finally:
        ctl.close()

    return CLIIdentitySyncSummary(
        enabled=True,
        identity_root=str(identity_root),
        profile_files_count=len(profile_paths),
        synced_profiles_count=len(synced_profiles),
        synced_profiles=tuple(synced_profiles),
    )


def _discover_profile_yaml_paths(identity_root: Path) -> list[Path]:
    return [
        profile_yaml.resolve()
        for child in sorted(identity_root.iterdir(), key=lambda item: item.name)
        if child.is_dir()
        for profile_yaml in (child / "profile.yaml",)
        if profile_yaml.is_file()
    ]


def _resolve_cli_identity_root(*, config: Any, roots: CLIRoots) -> Path:
    env_root = str(roots.env.get(OPENMINION_IDENTITY_ROOT_ENV, "") or "").strip()
    if env_root:
        return resolve_identity_root_from_env(
            env=roots.env,
            home_root=roots.home_root,
        )

    identity_cfg = getattr(config, "identity", None)
    configured_root = str(getattr(identity_cfg, "root", "") or "").strip()
    if configured_root and Path(configured_root).suffix.lower() != ".db":
        candidate = Path(configured_root).expanduser()
        if not candidate.is_absolute():
            candidate = roots.data_root / candidate
        return candidate.resolve(strict=False)

    return resolve_identity_root_from_env(
        env=roots.env,
        home_root=roots.home_root,
    )
