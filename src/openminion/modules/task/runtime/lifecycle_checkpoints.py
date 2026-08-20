# mypy: ignore-errors
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.base.time import utc_now_iso as _utc_now_iso

from .lifecycle_models import (
    ProjectCycleClaim,
    ProjectCycleClaimUnavailable,
    StaleProjectCycleClaim,
    _dump_metadata,
    _dump_state_blob,
    _load_metadata,
    _load_state_blob,
)


class TaskLifecycleRepositoryCheckpointMixin:
    def get_project_cycle_claim(self, task_id: str) -> ProjectCycleClaim | None:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM project_cycle_claims WHERE task_id = ?",
                (normalized_task_id,),
            ).fetchone()
        return self._claim_from_row(row) if row is not None else None

    def acquire_project_cycle_claim(
        self,
        *,
        task_id: str,
        owner_id: str,
        expected_checkpoint_id: str | None,
        ttl_seconds: int = 120,
        now: datetime | None = None,
    ) -> ProjectCycleClaim:
        normalized_task_id = str(task_id or "").strip()
        normalized_owner_id = str(owner_id or "").strip()
        expected = str(expected_checkpoint_id or "").strip() or None
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_owner_id:
            raise ValueError("owner_id is required")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be greater than zero")

        acquired_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires_at = acquired_at + timedelta(seconds=ttl_seconds)
        with self._lock, self._conn:
            latest = self._latest_checkpoint_id_locked(normalized_task_id)
            if latest != expected:
                raise StaleProjectCycleClaim(
                    f"expected checkpoint {expected!r}, found {latest!r}"
                )
            row = self._conn.execute(
                "SELECT * FROM project_cycle_claims WHERE task_id = ?",
                (normalized_task_id,),
            ).fetchone()
            if row is not None:
                existing = self._claim_from_row(row)
                if (
                    self._parse_claim_time(existing.expires_at) > acquired_at
                    and existing.owner_id != normalized_owner_id
                ):
                    raise ProjectCycleClaimUnavailable(
                        f"project cycle is already claimed: {normalized_task_id}"
                    )
                if (
                    existing.owner_id == normalized_owner_id
                    and existing.expected_checkpoint_id == expected
                    and self._parse_claim_time(existing.expires_at) > acquired_at
                ):
                    return existing
                fence_token = existing.fence_token + 1
            else:
                fence_token = 1
            self._conn.execute(
                """
                INSERT INTO project_cycle_claims(
                    task_id, owner_id, fence_token, expected_checkpoint_id, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    fence_token = excluded.fence_token,
                    expected_checkpoint_id = excluded.expected_checkpoint_id,
                    expires_at = excluded.expires_at
                """,
                (
                    normalized_task_id,
                    normalized_owner_id,
                    fence_token,
                    expected,
                    expires_at.isoformat(),
                ),
            )
        return ProjectCycleClaim(
            task_id=normalized_task_id,
            owner_id=normalized_owner_id,
            fence_token=fence_token,
            expected_checkpoint_id=expected,
            expires_at=expires_at.isoformat(),
        )

    def refresh_project_cycle_claim(
        self,
        claim: ProjectCycleClaim,
        *,
        ttl_seconds: int = 120,
        now: datetime | None = None,
    ) -> ProjectCycleClaim:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be greater than zero")
        refreshed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires_at = refreshed_at + timedelta(seconds=ttl_seconds)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM project_cycle_claims WHERE task_id = ?",
                (claim.task_id,),
            ).fetchone()
            self._require_matching_claim(row, claim, at=refreshed_at)
            self._conn.execute(
                "UPDATE project_cycle_claims SET expires_at = ? WHERE task_id = ?",
                (expires_at.isoformat(), claim.task_id),
            )
        return ProjectCycleClaim(
            task_id=claim.task_id,
            owner_id=claim.owner_id,
            fence_token=claim.fence_token,
            expected_checkpoint_id=claim.expected_checkpoint_id,
            expires_at=expires_at.isoformat(),
        )

    def release_project_cycle_claim(
        self,
        claim: ProjectCycleClaim,
        *,
        now: datetime | None = None,
    ) -> None:
        released_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM project_cycle_claims WHERE task_id = ?",
                (claim.task_id,),
            ).fetchone()
            self._require_matching_claim(row, claim, at=None)
            self._conn.execute(
                "UPDATE project_cycle_claims SET expires_at = ? WHERE task_id = ?",
                (released_at.isoformat(), claim.task_id),
            )

    def commit_project_cycle_checkpoint(
        self,
        claim: ProjectCycleClaim,
        *,
        checkpoint_id: str,
        state: Mapping[str, Any],
        now: datetime | None = None,
    ) -> None:
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_checkpoint_id:
            raise ValueError("checkpoint_id is required")
        committed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM project_cycle_claims WHERE task_id = ?",
                (claim.task_id,),
            ).fetchone()
            self._require_matching_claim(row, claim, at=committed_at)
            latest = self._latest_checkpoint_id_locked(claim.task_id)
            if latest != claim.expected_checkpoint_id:
                raise StaleProjectCycleClaim(
                    f"expected checkpoint {claim.expected_checkpoint_id!r}, "
                    f"found {latest!r}"
                )
            self._conn.execute(
                """
                INSERT INTO task_checkpoints(
                    task_id, checkpoint_id, created_at, state_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    claim.task_id,
                    normalized_checkpoint_id,
                    committed_at.isoformat(),
                    _dump_state_blob(state),
                ),
            )
            task_row = self._conn.execute(
                "SELECT metadata FROM scheduled_tasks WHERE task_id = ?",
                (claim.task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(f"task not found: {claim.task_id}")
            metadata = _load_metadata(task_row["metadata"])
            metadata["last_checkpoint_id"] = normalized_checkpoint_id
            self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET metadata = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    _dump_metadata(metadata),
                    committed_at.isoformat(),
                    claim.task_id,
                ),
            )

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> None:
        normalized_task_id = str(task_id or "").strip()
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_checkpoint_id:
            raise ValueError("checkpoint_id is required")
        if self.get(normalized_task_id) is None:
            raise KeyError(f"task not found: {normalized_task_id}")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO task_checkpoints(
                    task_id,
                    checkpoint_id,
                    created_at,
                    state_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized_task_id,
                    normalized_checkpoint_id,
                    _utc_now_iso(),
                    _dump_state_blob(state),
                ),
            )
            self._conn.commit()

    def get_latest_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT checkpoint_id, state_json
                FROM task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at DESC, checkpoint_id DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return str(row["checkpoint_id"]), _load_state_blob(row["state_json"])

    def get_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
    ) -> dict[str, Any] | None:
        normalized_task_id = str(task_id or "").strip()
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_checkpoint_id:
            raise ValueError("checkpoint_id is required")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT state_json
                FROM task_checkpoints
                WHERE task_id = ?
                  AND checkpoint_id = ?
                LIMIT 1
                """,
                (normalized_task_id, normalized_checkpoint_id),
            ).fetchone()
        if row is None:
            return None
        return _load_state_blob(row["state_json"])

    def list_checkpoints(self, *, task_id: str) -> list[str]:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT checkpoint_id
                FROM task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at ASC, checkpoint_id ASC
                """,
                (normalized,),
            ).fetchall()
        return [str(row["checkpoint_id"]) for row in rows]

    def _latest_checkpoint_id_locked(self, task_id: str) -> str | None:
        row = self._conn.execute(
            """
            SELECT checkpoint_id
            FROM task_checkpoints
            WHERE task_id = ?
            ORDER BY created_at DESC, checkpoint_id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return str(row["checkpoint_id"]) if row is not None else None

    @staticmethod
    def _parse_claim_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _claim_from_row(row: Any) -> ProjectCycleClaim:
        return ProjectCycleClaim(
            task_id=str(row["task_id"]),
            owner_id=str(row["owner_id"]),
            fence_token=int(row["fence_token"]),
            expected_checkpoint_id=(
                str(row["expected_checkpoint_id"])
                if row["expected_checkpoint_id"] is not None
                else None
            ),
            expires_at=str(row["expires_at"]),
        )

    def _require_matching_claim(
        self,
        row: Any,
        claim: ProjectCycleClaim,
        *,
        at: datetime | None,
    ) -> ProjectCycleClaim:
        if row is None:
            raise StaleProjectCycleClaim("project cycle claim does not exist")
        current = self._claim_from_row(row)
        if (
            current.owner_id != claim.owner_id
            or current.fence_token != claim.fence_token
            or current.expected_checkpoint_id != claim.expected_checkpoint_id
        ):
            raise StaleProjectCycleClaim("project cycle claim is stale")
        if at is not None and self._parse_claim_time(current.expires_at) <= at:
            raise StaleProjectCycleClaim("project cycle claim expired")
        return current
