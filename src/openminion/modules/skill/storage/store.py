"""Skill storage store implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openminion.modules.skill.storage.base import SkillStore
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .migrations import list_migrations


def _create_skill_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            scope TEXT NOT NULL,
            agent_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_versions (
            skill_id TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            source_artifact_ref TEXT NOT NULL,
            package_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (skill_id, version_hash),
            FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_index (
            skill_id TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            tools_json TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            applies_to_json TEXT NOT NULL,
            PRIMARY KEY (skill_id, version_hash),
            FOREIGN KEY(skill_id, version_hash) REFERENCES skill_versions(skill_id, version_hash)
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            used_for TEXT NOT NULL,
            outcome TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(skill_id, version_hash) REFERENCES skill_versions(skill_id, version_hash)
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skills_scope ON skills(scope, agent_id)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skills_updated ON skills(updated_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_versions_skill ON skill_versions(skill_id, created_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_runs_skill ON skill_runs(skill_id, created_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_runs_session ON skill_runs(session_id, created_at)"
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_proposals (
            proposal_id TEXT PRIMARY KEY,
            source_task_shape_ref TEXT NOT NULL,
            proposer_policy_id TEXT NOT NULL,
            proposed_at TEXT NOT NULL,
            proposal_json TEXT NOT NULL,
            queue_state TEXT NOT NULL,
            applied_addition_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_proposal_reviews (
            proposal_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            review_policy_id TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            review_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(proposal_id) REFERENCES skill_proposals(proposal_id)
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_proposals_state ON skill_proposals(queue_state, created_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_proposals_shape ON skill_proposals(source_task_shape_ref)"
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_suggestion_audit (
            event_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            signature TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason TEXT,
            outcome TEXT,
            surfaced_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_audit_signature ON skill_suggestion_audit(signature, surfaced_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_audit_event ON skill_suggestion_audit(event_type, surfaced_at)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_audit_proposal ON skill_suggestion_audit(proposal_id, surfaced_at)"
    )


class _SkillStoreMixin(SkillStore):
    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def upsert_skill(
        self,
        *,
        skill_id: str,
        name: str,
        status: str,
        scope: str,
        agent_id: str | None,
        ts: str,
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO skills(skill_id, name, status, scope, agent_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_id) DO UPDATE SET
                name=excluded.name,
                status=excluded.status,
                scope=excluded.scope,
                agent_id=excluded.agent_id,
                updated_at=excluded.updated_at
            """,
            (skill_id, name, status, scope, agent_id, ts, ts),
        )

    def insert_skill_version(
        self,
        *,
        skill_id: str,
        version_hash: str,
        source_artifact_ref: str,
        package_json: str,
        created_at: str,
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO skill_versions(skill_id, version_hash, source_artifact_ref, package_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(skill_id, version_hash) DO NOTHING
            """,
            (
                skill_id,
                version_hash,
                source_artifact_ref,
                package_json,
                created_at,
            ),
        )

    def upsert_skill_index(
        self,
        *,
        skill_id: str,
        version_hash: str,
        tags_json: str,
        tools_json: str,
        keywords_json: str,
        applies_to_json: str,
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO skill_index(skill_id, version_hash, tags_json, tools_json, keywords_json, applies_to_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_id, version_hash) DO UPDATE SET
                tags_json=excluded.tags_json,
                tools_json=excluded.tools_json,
                keywords_json=excluded.keywords_json,
                applies_to_json=excluded.applies_to_json
            """,
            (
                skill_id,
                version_hash,
                tags_json,
                tools_json,
                keywords_json,
                applies_to_json,
            ),
        )

    def get_skill_package(
        self, skill_id: str, version_hash: str | None = None
    ) -> dict[str, Any] | None:
        if version_hash:
            rows = self._record_store.query_dicts(
                """
                SELECT package_json
                FROM skill_versions
                WHERE skill_id = ? AND version_hash = ?
                LIMIT 1
                """,
                (skill_id, version_hash),
            )
        else:
            rows = self._record_store.query_dicts(
                """
                SELECT package_json
                FROM skill_versions
                WHERE skill_id = ?
                ORDER BY created_at DESC, version_hash DESC
                LIMIT 1
                """,
                (skill_id,),
            )
        if not rows:
            return None
        return _json_loads(str(rows[0]["package_json"]), {})

    def list_latest_skills(
        self,
        *,
        status_filter: list[str] | None = None,
        agent_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses: list[str] = []
        params: list[Any] = []

        if status_filter:
            placeholders = ",".join(["?"] * len(status_filter))
            where_clauses.append(f"s.status IN ({placeholders})")
            params.extend(status_filter)

        if scopes:
            placeholders = ",".join(["?"] * len(scopes))
            where_clauses.append(f"s.scope IN ({placeholders})")
            params.extend(scopes)

        if agent_id:
            where_clauses.append(
                "(s.scope = 'global' OR (s.scope = 'agent' AND (s.agent_id IS NULL OR s.agent_id = ?)))"
            )
            params.append(agent_id)
        else:
            where_clauses.append("s.scope = 'global'")

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        rows = self._record_store.query_dicts(
            f"""
            SELECT
                s.skill_id,
                s.name,
                s.status,
                s.scope,
                s.agent_id,
                sv.version_hash,
                sv.package_json,
                sv.created_at,
                si.tags_json,
                si.tools_json,
                si.keywords_json,
                si.applies_to_json
            FROM skills s
            JOIN skill_versions sv ON sv.skill_id = s.skill_id
            LEFT JOIN skill_index si ON si.skill_id = sv.skill_id AND si.version_hash = sv.version_hash
            {where_sql}
            ORDER BY s.skill_id ASC, sv.created_at DESC, sv.version_hash DESC
            """,
            params,
        )

        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            skill_key = str(row["skill_id"])
            if skill_key in latest:
                continue
            latest[skill_key] = {
                "skill_id": skill_key,
                "name": str(row["name"]),
                "status": str(row["status"]),
                "scope": str(row["scope"]),
                "agent_id": row["agent_id"],
                "version_hash": str(row["version_hash"]),
                "package": _json_loads(str(row["package_json"]), {}),
                "created_at": str(row["created_at"]),
                "tags": _json_loads(row["tags_json"], []),
                "tools": _json_loads(row["tools_json"], []),
                "keywords": _json_loads(row["keywords_json"], []),
                "applies_to": _json_loads(row["applies_to_json"], {}),
            }

        return sorted(latest.values(), key=lambda item: item["skill_id"])

    def list_skills(
        self,
        *,
        status_filter: list[str] | None = None,
        scope: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scopes = [scope] if scope else None
        return self.list_latest_skills(
            status_filter=status_filter, agent_id=agent_id, scopes=scopes
        )

    def insert_skill_run(
        self,
        *,
        run_id: str,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs_json: str,
        created_at: str,
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO skill_runs(
                run_id, session_id, agent_id, skill_id, version_hash,
                used_for, outcome, evidence_refs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                session_id,
                agent_id,
                skill_id,
                version_hash,
                used_for,
                outcome,
                evidence_refs_json,
                created_at,
            ),
        )

    def delete_skill(
        self,
        *,
        skill_id: str,
        version_hash: str | None = None,
    ) -> dict[str, int]:
        with self._record_store.transaction():
            if version_hash:
                runs = self._record_store.execute_count(
                    "DELETE FROM skill_runs WHERE skill_id = ? AND version_hash = ?",
                    (skill_id, version_hash),
                )
                indexes = self._record_store.execute_count(
                    "DELETE FROM skill_index WHERE skill_id = ? AND version_hash = ?",
                    (skill_id, version_hash),
                )
                versions = self._record_store.execute_count(
                    "DELETE FROM skill_versions WHERE skill_id = ? AND version_hash = ?",
                    (skill_id, version_hash),
                )
                remaining = self._record_store.query_dicts(
                    "SELECT 1 FROM skill_versions WHERE skill_id = ? LIMIT 1",
                    (skill_id,),
                )
                skills = 0
                if not remaining:
                    skills = self._record_store.execute_count(
                        "DELETE FROM skills WHERE skill_id = ?",
                        (skill_id,),
                    )
            else:
                runs = self._record_store.execute_count(
                    "DELETE FROM skill_runs WHERE skill_id = ?",
                    (skill_id,),
                )
                indexes = self._record_store.execute_count(
                    "DELETE FROM skill_index WHERE skill_id = ?",
                    (skill_id,),
                )
                versions = self._record_store.execute_count(
                    "DELETE FROM skill_versions WHERE skill_id = ?",
                    (skill_id,),
                )
                skills = self._record_store.execute_count(
                    "DELETE FROM skills WHERE skill_id = ?",
                    (skill_id,),
                )
        return {
            "skills": int(skills or 0),
            "versions": int(versions or 0),
            "index": int(indexes or 0),
            "runs": int(runs or 0),
        }

    def create_proposal(
        self,
        *,
        proposal_id: str,
        source_task_shape_ref: str,
        proposer_policy_id: str,
        proposed_at: str,
        proposal_json: str,
        created_at: str,
    ) -> bool:
        affected = self._record_store.execute_count(
            """
            INSERT OR IGNORE INTO skill_proposals(
                proposal_id, source_task_shape_ref, proposer_policy_id,
                proposed_at, proposal_json, queue_state,
                applied_addition_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
            """,
            (
                proposal_id,
                source_task_shape_ref,
                proposer_policy_id,
                proposed_at,
                proposal_json,
                created_at,
                created_at,
            ),
        )
        return int(affected or 0) > 0

    def list_proposals(
        self,
        *,
        queue_state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        if queue_state:
            rows = self._record_store.query_dicts(
                """
                SELECT
                    p.proposal_id,
                    p.source_task_shape_ref,
                    p.proposer_policy_id,
                    p.proposed_at,
                    p.proposal_json,
                    p.queue_state,
                    p.applied_addition_json,
                    p.created_at,
                    p.updated_at,
                    r.review_json,
                    r.reviewer_id,
                    r.status AS review_status,
                    r.decided_at
                FROM skill_proposals p
                LEFT JOIN skill_proposal_reviews r ON r.proposal_id = p.proposal_id
                WHERE p.queue_state = ?
                ORDER BY p.created_at ASC, p.proposal_id ASC
                LIMIT ?
                """,
                (str(queue_state), safe_limit),
            )
        else:
            rows = self._record_store.query_dicts(
                """
                SELECT
                    p.proposal_id,
                    p.source_task_shape_ref,
                    p.proposer_policy_id,
                    p.proposed_at,
                    p.proposal_json,
                    p.queue_state,
                    p.applied_addition_json,
                    p.created_at,
                    p.updated_at,
                    r.review_json,
                    r.reviewer_id,
                    r.status AS review_status,
                    r.decided_at
                FROM skill_proposals p
                LEFT JOIN skill_proposal_reviews r ON r.proposal_id = p.proposal_id
                ORDER BY p.created_at ASC, p.proposal_id ASC
                LIMIT ?
                """,
                (safe_limit,),
            )
        return [_proposal_row(row) for row in rows]

    def get_proposal(
        self,
        *,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        rows = self._record_store.query_dicts(
            """
            SELECT
                p.proposal_id,
                p.source_task_shape_ref,
                p.proposer_policy_id,
                p.proposed_at,
                p.proposal_json,
                p.queue_state,
                p.applied_addition_json,
                p.created_at,
                p.updated_at,
                r.review_json,
                r.reviewer_id,
                r.status AS review_status,
                r.decided_at
            FROM skill_proposals p
            LEFT JOIN skill_proposal_reviews r ON r.proposal_id = p.proposal_id
            WHERE p.proposal_id = ?
            LIMIT 1
            """,
            (str(proposal_id),),
        )
        if not rows:
            return None
        return _proposal_row(rows[0])

    def record_proposal_review(
        self,
        *,
        proposal_id: str,
        status: str,
        reviewer_id: str,
        review_policy_id: str,
        decided_at: str,
        review_json: str,
        created_at: str,
    ) -> None:
        with self._record_store.transaction():
            existing = self._record_store.query_dicts(
                "SELECT queue_state FROM skill_proposals WHERE proposal_id = ?",
                (str(proposal_id),),
            )
            if not existing:
                raise ValueError(f"proposal not found for review: {proposal_id!r}")
            current_state = str(existing[0].get("queue_state") or "")
            if current_state == "applied":
                raise ValueError(
                    f"proposal already applied; cannot record new review: {proposal_id!r}"
                )
            self._record_store.execute_count(
                """
                INSERT INTO skill_proposal_reviews(
                    proposal_id, status, reviewer_id, review_policy_id,
                    decided_at, review_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status=excluded.status,
                    reviewer_id=excluded.reviewer_id,
                    review_policy_id=excluded.review_policy_id,
                    decided_at=excluded.decided_at,
                    review_json=excluded.review_json
                """,
                (
                    str(proposal_id),
                    str(status),
                    str(reviewer_id),
                    str(review_policy_id),
                    str(decided_at),
                    str(review_json),
                    str(created_at),
                ),
            )
            self._record_store.execute_count(
                """
                UPDATE skill_proposals
                SET queue_state = 'reviewed', updated_at = ?
                WHERE proposal_id = ?
                """,
                (str(created_at), str(proposal_id)),
            )

    def apply_proposal(
        self,
        *,
        proposal_id: str,
        applied_at: str,
        applied_addition_json: str,
    ) -> None:
        with self._record_store.transaction():
            existing = self._record_store.query_dicts(
                "SELECT queue_state FROM skill_proposals WHERE proposal_id = ?",
                (str(proposal_id),),
            )
            if not existing:
                raise ValueError(f"proposal not found for apply: {proposal_id!r}")
            current_state = str(existing[0].get("queue_state") or "")
            if current_state != "reviewed":
                raise ValueError(
                    "apply requires queue_state='reviewed'; "
                    f"got {current_state!r} for {proposal_id!r}"
                )
            self._record_store.execute_count(
                """
                UPDATE skill_proposals
                SET queue_state = 'applied',
                    applied_addition_json = ?,
                    updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    str(applied_addition_json),
                    str(applied_at),
                    str(proposal_id),
                ),
            )

    def record_suggestion_event(
        self,
        *,
        event_id: str,
        proposal_id: str,
        signature: str,
        event_type: str,
        reason: str | None,
        outcome: str | None,
        surfaced_at: str,
    ) -> None:
        self._record_store.execute_count(
            """
            INSERT INTO skill_suggestion_audit(
                event_id, proposal_id, signature, event_type,
                reason, outcome, surfaced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event_id),
                str(proposal_id),
                str(signature),
                str(event_type),
                reason if reason is None else str(reason),
                outcome if outcome is None else str(outcome),
                str(surfaced_at),
            ),
        )

    def latest_surfaced_at_for_signature(
        self,
        *,
        signature: str,
    ) -> str | None:
        rows = self._record_store.query_dicts(
            """
            SELECT MAX(surfaced_at) AS last_surfaced_at
            FROM skill_suggestion_audit
            WHERE signature = ? AND event_type = 'surfaced'
            """,
            (str(signature),),
        )
        if not rows:
            return None
        value = rows[0].get("last_surfaced_at")
        return str(value) if value not in {None, ""} else None

    def count_suggestion_events(self) -> dict[str, Any]:
        surfaced_rows = self._record_store.query_dicts(
            """
            SELECT COUNT(*) AS n, MAX(surfaced_at) AS last
            FROM skill_suggestion_audit
            WHERE event_type = 'surfaced'
            """,
            (),
        )
        outcome_rows = self._record_store.query_dicts(
            """
            SELECT outcome, COUNT(*) AS n
            FROM skill_suggestion_audit
            WHERE event_type = 'outcome_recorded'
            GROUP BY outcome
            """,
            (),
        )
        dismiss_rows = self._record_store.query_dicts(
            """
            SELECT reason, COUNT(*) AS n
            FROM skill_suggestion_audit
            WHERE event_type = 'auto_dismissed'
            GROUP BY reason
            """,
            (),
        )
        last_outcome_rows = self._record_store.query_dicts(
            """
            SELECT MAX(surfaced_at) AS last
            FROM skill_suggestion_audit
            WHERE event_type = 'outcome_recorded'
            """,
            (),
        )
        pending_rows = self._record_store.query_dicts(
            "SELECT COUNT(*) AS n FROM skill_proposals WHERE queue_state = 'pending'",
            (),
        )
        outcomes: dict[str, int] = {"accepted": 0, "rejected": 0, "deferred": 0}
        for row in outcome_rows:
            key = str(row.get("outcome") or "")
            if key in outcomes:
                outcomes[key] = int(row.get("n") or 0)
        dismiss_reasons: dict[str, int] = {}
        auto_dismissed_total = 0
        for row in dismiss_rows:
            key = str(row.get("reason") or "")
            count = int(row.get("n") or 0)
            if key:
                dismiss_reasons[key] = count
            auto_dismissed_total += count
        return {
            "surfaced_count": int(
                (surfaced_rows[0].get("n") if surfaced_rows else 0) or 0
            ),
            "last_surfaced_at": str(
                (surfaced_rows[0].get("last") if surfaced_rows else "") or ""
            ),
            "accepted_count": outcomes["accepted"],
            "rejected_count": outcomes["rejected"],
            "deferred_count": outcomes["deferred"],
            "auto_dismissed_count": auto_dismissed_total,
            "auto_dismiss_reasons": dismiss_reasons,
            "last_outcome_at": str(
                (last_outcome_rows[0].get("last") if last_outcome_rows else "") or ""
            ),
            "pending_count": int(
                (pending_rows[0].get("n") if pending_rows else 0) or 0
            ),
        }


class SQLiteSkillStore(BaseModuleSQLiteStore, _SkillStoreMixin):
    def __init__(
        self,
        sqlite_path: str | Path | None = None,
        *,
        wal: bool = True,
        record_store: RecordStore | None = None,
    ) -> None:
        super().__init__(sqlite_path, wal=wal, record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_skill_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresSkillStore(BaseModuleStore, _SkillStoreMixin):
    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_skill_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


def _json_loads(raw: Any, fallback: Any) -> Any:
    if raw in {None, ""}:
        return fallback
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return fallback


def _proposal_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project one SQL row into the typed proposal-view shape."""

    proposal_payload = _json_loads(str(row.get("proposal_json") or "{}"), {})
    applied_addition_raw = row.get("applied_addition_json")
    applied_addition = (
        _json_loads(str(applied_addition_raw), None)
        if applied_addition_raw not in {None, ""}
        else None
    )
    review_raw = row.get("review_json")
    review_payload = (
        _json_loads(str(review_raw), None) if review_raw not in {None, ""} else None
    )
    return {
        "proposal_id": str(row.get("proposal_id") or ""),
        "source_task_shape_ref": str(row.get("source_task_shape_ref") or ""),
        "proposer_policy_id": str(row.get("proposer_policy_id") or ""),
        "proposed_at": str(row.get("proposed_at") or ""),
        "proposal": proposal_payload,
        "queue_state": str(row.get("queue_state") or ""),
        "applied_addition": applied_addition,
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "review": review_payload,
        "reviewer_id": str(row.get("reviewer_id") or "")
        if row.get("reviewer_id") is not None
        else "",
        "review_status": str(row.get("review_status") or "")
        if row.get("review_status") is not None
        else "",
        "decided_at": str(row.get("decided_at") or "")
        if row.get("decided_at") is not None
        else "",
    }


__all__ = ("PostgresSkillStore", "SQLiteSkillStore")
