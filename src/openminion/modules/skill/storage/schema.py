from openminion.modules.storage.record_store import RecordStore


def create_skill_schema(record_store: RecordStore) -> None:
    _create_catalog_schema(record_store)
    _create_proposal_schema(record_store)


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
