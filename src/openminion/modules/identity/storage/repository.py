from pathlib import Path
from typing import Any

from ..interfaces import (
    IDENTITY_REPOSITORY_INTERFACE_VERSION,
    ensure_identity_repository_compatibility,
)
from ..runtime.service import IdentityCtl
from .store import SQLiteIdentityStore


class SQLiteIdentityRepository:
    """Identity repository adapter backed by SQLite identity storage."""

    repository_contract_version = IDENTITY_REPOSITORY_INTERFACE_VERSION

    def __init__(self, *, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path).expanduser().resolve(strict=False)
        self._ctl = IdentityCtl(
            store=SQLiteIdentityStore(sqlite_path=str(self.sqlite_path))
        )

    def get_profile(self, agent_id: str) -> Any | None:
        return self._ctl.get_profile(agent_id)

    def upsert_profile(
        self,
        profile: Any,
        actor: str | None = None,
        reason: str | None = None,
    ) -> str:
        return self._ctl.upsert_profile(profile, actor=actor, reason=reason)

    def close(self) -> None:
        self._ctl.close()


def create_sqlite_identity_repository(
    *, sqlite_path: str | Path
) -> SQLiteIdentityRepository:
    repo = SQLiteIdentityRepository(sqlite_path=sqlite_path)
    ensure_identity_repository_compatibility(repo)
    return repo
