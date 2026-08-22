from openminion.modules.storage.record_store import RecordStore


def _table_columns(record_store: RecordStore, table_name: str) -> set[str]:
    if bool(record_store.capabilities().get("raw_sql")):
        rows = record_store.query_dicts(f"PRAGMA table_info({table_name})")
    else:
        rows = record_store.query_dicts(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table_name,),
        )
    return {str(row["name"]) for row in rows}


def _ensure_column(
    record_store: RecordStore,
    *,
    table_name: str,
    column_name: str,
    ddl_tail: str,
) -> None:
    columns = _table_columns(record_store, table_name)
    if not columns or column_name in columns:
        return
    record_store.execute_count(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_tail}"
    )


def create_skill_schema(record_store: RecordStore) -> None:
    _create_catalog_schema(record_store)
    _create_proposal_schema(record_store)


def _upgrade_legacy_catalog_columns(record_store: RecordStore) -> None:
    _ensure_column(
        record_store,
        table_name="skills",
        column_name="active_version_hash",
        ddl_tail="TEXT",
    )
    _ensure_column(
        record_store,
        table_name="skill_versions",
        column_name="content_fingerprint",
        ddl_tail="TEXT NOT NULL DEFAULT ''",
    )
    record_store.execute_count(
        "UPDATE skill_versions "
        "SET content_fingerprint = 'legacy:' || version_hash "
        "WHERE content_fingerprint = ''"
    )


def _backfill_legacy_catalog_admission(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        UPDATE skills SET active_version_hash = (
            SELECT version_hash FROM skill_versions
            WHERE skill_versions.skill_id = skills.skill_id
            ORDER BY created_at DESC, version_hash DESC LIMIT 1
        ) WHERE active_version_hash IS NULL
        """
    )
    record_store.execute_count(
        """
        INSERT INTO skill_version_admissions(
            skill_id, version_hash, state, target_status, content_fingerprint,
            authority_class, reviewer_id, reason, created_at, decided_at
        )
        SELECT s.skill_id, s.active_version_hash, 'legacy_grandfathered', s.status,
               sv.content_fingerprint, 'legacy_grandfathered', NULL,
               'pre-admission catalog version', s.updated_at, s.updated_at
        FROM skills s JOIN skill_versions sv
          ON sv.skill_id = s.skill_id AND sv.version_hash = s.active_version_hash
        ON CONFLICT(skill_id, version_hash) DO NOTHING
        """
    )


def _create_catalog_indexes(record_store: RecordStore) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)",
        "CREATE INDEX IF NOT EXISTS idx_skills_scope ON skills(scope, agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_skills_updated ON skills(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_skill_versions_skill "
        "ON skill_versions(skill_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_skill_runs_skill "
        "ON skill_runs(skill_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_skill_runs_session "
        "ON skill_runs(session_id, created_at)",
    )
    for statement in statements:
        record_store.execute_count(statement)


def _create_catalog_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            scope TEXT NOT NULL,
            agent_id TEXT,
            active_version_hash TEXT,
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
            content_fingerprint TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (skill_id, version_hash),
            FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
        )
    """
    )
    _upgrade_legacy_catalog_columns(record_store)
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_skill_versions_fingerprint "
        "ON skill_versions(skill_id, content_fingerprint)"
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS skill_version_admissions (
            skill_id TEXT NOT NULL,
            version_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            target_status TEXT NOT NULL,
            content_fingerprint TEXT NOT NULL,
            authority_class TEXT NOT NULL,
            reviewer_id TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            PRIMARY KEY (skill_id, version_hash),
            FOREIGN KEY(skill_id, version_hash) REFERENCES skill_versions(skill_id, version_hash)
        )
        """
    )
    _backfill_legacy_catalog_admission(record_store)
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
    _create_catalog_indexes(record_store)


def _create_proposal_schema(record_store: RecordStore) -> None:
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
