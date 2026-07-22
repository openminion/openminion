import json
from pathlib import Path
from collections.abc import Mapping

from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.storage.base import (
    CachedSnippet,
    IdentityStore,
    StoredProfile,
)
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .migrations import list_migrations

from openminion.base.time import utc_now_iso as _iso_now


def _parse_json(raw: str | None, fallback: object) -> object:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _create_identity_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS identity_profiles (
            agent_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL,
            profile_revision INTEGER NOT NULL,
            profile_version TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS identity_snippet_cache (
            cache_key TEXT PRIMARY KEY,
            snippet_text TEXT NOT NULL,
            used_tokens INTEGER,
            used_chars INTEGER,
            sections_json TEXT,
            included_fields_json TEXT,
            omitted_fields_json TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_identity_profiles_updated_at ON identity_profiles(updated_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_identity_snippet_cache_updated_at ON identity_snippet_cache(updated_at)"
    )


def _stored_profile_from_row(row: Mapping[str, object]) -> StoredProfile:
    profile = AgentProfile.model_validate(json.loads(str(row["profile_json"])))
    return StoredProfile(
        agent_id=str(row["agent_id"]),
        profile=profile,
        profile_revision=int(row["profile_revision"]),
        profile_version=str(row["profile_version"]),
        updated_at=str(row["updated_at"]),
    )


class _IdentityStoreMixin(IdentityStore):
    def get_profile(self, agent_id: str) -> StoredProfile | None:
        rows = self._record_store.query_dicts(
            """
            SELECT agent_id, profile_json, profile_revision, profile_version, updated_at
            FROM identity_profiles
            WHERE agent_id = ?
            LIMIT 1
            """,
            (agent_id,),
        )
        return _stored_profile_from_row(rows[0]) if rows else None

    def list_profiles(self) -> list[StoredProfile]:
        rows = self._record_store.query_dicts(
            """
            SELECT agent_id, profile_json, profile_revision, profile_version, updated_at
            FROM identity_profiles
            ORDER BY agent_id ASC
            """
        )
        return [_stored_profile_from_row(row) for row in rows]

    def upsert_profile(self, profile: AgentProfile, profile_version: str) -> None:
        payload = json.dumps(
            profile.model_dump(mode="json"), sort_keys=True, ensure_ascii=True
        )
        self._record_store.execute_count(
            """
            INSERT INTO identity_profiles(agent_id, profile_json, profile_revision, profile_version, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                profile_revision=excluded.profile_revision,
                profile_version=excluded.profile_version,
                updated_at=excluded.updated_at
            """,
            (
                profile.agent_id,
                payload,
                int(profile.profile_revision),
                profile_version,
                _iso_now(),
            ),
        )

    def update_profile_version(self, agent_id: str, profile_version: str) -> None:
        self._record_store.execute_count(
            """
            UPDATE identity_profiles
            SET profile_version = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (profile_version, _iso_now(), agent_id),
        )

    def delete_profile(self, agent_id: str) -> None:
        self._record_store.delete_rows("identity_profiles", {"agent_id": agent_id})

    def get_cached_snippet(self, cache_key: str) -> CachedSnippet | None:
        rows = self._record_store.query_dicts(
            """
            SELECT cache_key, snippet_text, used_tokens, used_chars,
                   sections_json, included_fields_json, omitted_fields_json, warnings_json, updated_at
            FROM identity_snippet_cache
            WHERE cache_key = ?
            LIMIT 1
            """,
            (cache_key,),
        )
        if not rows:
            return None
        row = rows[0]
        raw_sections = _parse_json(str(row["sections_json"]), {})
        sections = (
            {
                str(key): str(value)
                for key, value in raw_sections.items()
                if isinstance(key, str) and isinstance(value, str) and value.strip()
            }
            if isinstance(raw_sections, dict)
            else {}
        )
        included = _parse_json(str(row["included_fields_json"]), [])
        omitted = _parse_json(str(row["omitted_fields_json"]), [])
        warnings = _parse_json(str(row["warnings_json"]), [])
        return CachedSnippet(
            cache_key=str(row["cache_key"]),
            snippet_text=str(row["snippet_text"]),
            used_tokens=int(row["used_tokens"] or 0),
            used_chars=int(row["used_chars"] or 0),
            sections=sections or None,
            included_fields=[str(item) for item in included if isinstance(item, str)],
            omitted_fields=[str(item) for item in omitted if isinstance(item, str)],
            warnings=[str(item) for item in warnings if isinstance(item, str)],
            updated_at=str(row["updated_at"]),
        )

    def upsert_cached_snippet(
        self,
        *,
        cache_key: str,
        snippet_text: str,
        used_tokens: int,
        used_chars: int,
        sections: dict[str, str] | None,
        included_fields: list[str],
        omitted_fields: list[str],
        warnings: list[str],
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO identity_snippet_cache(
                cache_key, snippet_text, used_tokens, used_chars,
                sections_json, included_fields_json, omitted_fields_json, warnings_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                snippet_text=excluded.snippet_text,
                used_tokens=excluded.used_tokens,
                used_chars=excluded.used_chars,
                sections_json=excluded.sections_json,
                included_fields_json=excluded.included_fields_json,
                omitted_fields_json=excluded.omitted_fields_json,
                warnings_json=excluded.warnings_json,
                updated_at=excluded.updated_at
            """,
            (
                cache_key,
                snippet_text,
                int(used_tokens),
                int(used_chars),
                json.dumps(sections or {}, ensure_ascii=True, sort_keys=True),
                json.dumps(included_fields, ensure_ascii=True),
                json.dumps(omitted_fields, ensure_ascii=True),
                json.dumps(warnings, ensure_ascii=True),
                _iso_now(),
            ),
        )

    def clear_cache(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self._record_store.execute_count("DELETE FROM identity_snippet_cache")
            return
        self._record_store.execute_count(
            "DELETE FROM identity_snippet_cache WHERE cache_key LIKE ?",
            (f"{agent_id}|%",),
        )


class SQLiteIdentityStore(BaseModuleSQLiteStore, _IdentityStoreMixin):
    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        super().__init__(sqlite_path, wal=wal, record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_identity_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresIdentityStore(BaseModuleStore, _IdentityStoreMixin):
    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_identity_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


__all__ = ["PostgresIdentityStore", "SQLiteIdentityStore"]
