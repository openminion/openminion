from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    """
    CREATE TABLE IF NOT EXISTS memory_records (
      id                 TEXT PRIMARY KEY,
      scope              TEXT NOT NULL,
      type               TEXT NOT NULL,
      key                TEXT,
      title              TEXT,
      content_json       TEXT NOT NULL,
      tags_json          TEXT NOT NULL DEFAULT '[]',
      entities_json      TEXT NOT NULL DEFAULT '[]',
      source             TEXT NOT NULL,
      confidence         REAL NOT NULL,
      evidence_json      TEXT NOT NULL DEFAULT '[]',
      meta_json          TEXT NOT NULL DEFAULT '{}',
      last_hit_at        TEXT,
      tier               TEXT NOT NULL DEFAULT 'working',
      access_count       INTEGER NOT NULL DEFAULT 0,
      expires_at         TEXT,
      created_at         TEXT NOT NULL,
      updated_at         TEXT NOT NULL,
      supersedes_id      TEXT,
      superseded_by_id   TEXT,
      supersession_reason TEXT,
      is_deleted         INTEGER NOT NULL DEFAULT 0,

      FOREIGN KEY(supersedes_id) REFERENCES memory_records(id),
      FOREIGN KEY(superseded_by_id) REFERENCES memory_records(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_entities (
      entity             TEXT NOT NULL,
      record_id          TEXT NOT NULL,
      scope              TEXT NOT NULL,
      type               TEXT NOT NULL,
      created_at         TEXT NOT NULL,

      PRIMARY KEY (entity, record_id),
      FOREIGN KEY(record_id) REFERENCES memory_records(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_candidates (
      candidate_id       TEXT PRIMARY KEY,
      session_id         TEXT NOT NULL,
      proposed_scope     TEXT NOT NULL,
      type               TEXT NOT NULL,
      key                TEXT,
      title              TEXT,
      content_json       TEXT NOT NULL,
      tags_json          TEXT NOT NULL DEFAULT '[]',
      entities_json      TEXT NOT NULL DEFAULT '[]',
      source             TEXT NOT NULL,
      confidence         REAL NOT NULL,
      evidence_json      TEXT NOT NULL DEFAULT '[]',
      meta_json          TEXT NOT NULL DEFAULT '{}',
      status             TEXT NOT NULL,
      review_json        TEXT,
      created_at         TEXT NOT NULL,
      updated_at         TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_tier_transitions (
      transition_id       TEXT PRIMARY KEY,
      record_id           TEXT NOT NULL,
      scope               TEXT NOT NULL,
      record_type         TEXT NOT NULL,
      from_tier           TEXT NOT NULL,
      to_tier             TEXT NOT NULL,
      transition_reason   TEXT NOT NULL,
      transition_at       TEXT NOT NULL,
      access_count        INTEGER NOT NULL DEFAULT 0,
      meta_json           TEXT NOT NULL DEFAULT '{}',

      FOREIGN KEY(record_id) REFERENCES memory_records(id)
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
      id UNINDEXED,
      scope,
      type,
      key,
      title,
      content_text,
      tags_text,
      entities_text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS migrations (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_candidates_scope_status ON memory_candidates(proposed_scope, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_session_status ON memory_candidates(session_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_entities_entity ON memory_entities(entity)",
    "CREATE INDEX IF NOT EXISTS idx_entities_scope_entity ON memory_entities(scope, entity)",
    "CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_record ON memory_tier_transitions(record_id, transition_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_scope ON memory_tier_transitions(scope, transition_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_records_confidence ON memory_records(confidence DESC)",
    "CREATE INDEX IF NOT EXISTS idx_records_scope_key ON memory_records(scope, type, key)",
    "CREATE INDEX IF NOT EXISTS idx_records_scope_tier_updated ON memory_records(scope, tier, updated_at DESC)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_records_scope_type_key_active
    ON memory_records(scope, type, key)
    WHERE key IS NOT NULL AND is_deleted = 0 AND superseded_by_id IS NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_records_scope_type_updated ON memory_records(scope, type, updated_at DESC)",
)

POSTGRES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS memory_records (
      id                 TEXT PRIMARY KEY,
      scope              TEXT NOT NULL,
      type               TEXT NOT NULL,
      key                TEXT,
      title              TEXT,
      content_json       JSONB NOT NULL,
      tags_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
      entities_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
      source             TEXT NOT NULL,
      confidence         DOUBLE PRECISION NOT NULL,
      evidence_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
      meta_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
      last_hit_at        TEXT,
      tier               TEXT NOT NULL DEFAULT 'working',
      access_count       INTEGER NOT NULL DEFAULT 0,
      expires_at         TEXT,
      created_at         TEXT NOT NULL,
      updated_at         TEXT NOT NULL,
      supersedes_id      TEXT,
      superseded_by_id   TEXT,
      supersession_reason TEXT,
      is_deleted         BOOLEAN NOT NULL DEFAULT FALSE,
      search_text        TEXT NOT NULL DEFAULT '',
      search_vector      TSVECTOR,

      FOREIGN KEY(supersedes_id) REFERENCES memory_records(id),
      FOREIGN KEY(superseded_by_id) REFERENCES memory_records(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_entities (
      entity             TEXT NOT NULL,
      record_id          TEXT NOT NULL,
      scope              TEXT NOT NULL,
      type               TEXT NOT NULL,
      created_at         TEXT NOT NULL,

      PRIMARY KEY (entity, record_id),
      FOREIGN KEY(record_id) REFERENCES memory_records(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_candidates (
      candidate_id       TEXT PRIMARY KEY,
      session_id         TEXT NOT NULL,
      proposed_scope     TEXT NOT NULL,
      type               TEXT NOT NULL,
      key                TEXT,
      title              TEXT,
      content_json       JSONB NOT NULL,
      tags_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
      entities_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
      source             TEXT NOT NULL,
      confidence         DOUBLE PRECISION NOT NULL,
      evidence_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
      meta_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
      status             TEXT NOT NULL,
      review_json        JSONB,
      created_at         TEXT NOT NULL,
      updated_at         TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_tier_transitions (
      transition_id       TEXT PRIMARY KEY,
      record_id           TEXT NOT NULL,
      scope               TEXT NOT NULL,
      record_type         TEXT NOT NULL,
      from_tier           TEXT NOT NULL,
      to_tier             TEXT NOT NULL,
      transition_reason   TEXT NOT NULL,
      transition_at       TEXT NOT NULL,
      access_count        INTEGER NOT NULL DEFAULT 0,
      meta_json           JSONB NOT NULL DEFAULT '{}'::jsonb,

      FOREIGN KEY(record_id) REFERENCES memory_records(id)
    )
    """,
    """
    CREATE OR REPLACE FUNCTION memory_records_search_trigger() RETURNS trigger AS $$
    BEGIN
      NEW.search_vector := to_tsvector('simple', COALESCE(NEW.search_text, ''));
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    DROP TRIGGER IF EXISTS trg_memory_records_search ON memory_records
    """,
    """
    CREATE TRIGGER trg_memory_records_search
      BEFORE INSERT OR UPDATE OF search_text ON memory_records
      FOR EACH ROW
      EXECUTE FUNCTION memory_records_search_trigger()
    """,
    """
    CREATE TABLE IF NOT EXISTS migrations (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_candidates_scope_status ON memory_candidates(proposed_scope, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_session_status ON memory_candidates(session_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_entities_entity ON memory_entities(entity)",
    "CREATE INDEX IF NOT EXISTS idx_entities_scope_entity ON memory_entities(scope, entity)",
    "CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_record ON memory_tier_transitions(record_id, transition_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_scope ON memory_tier_transitions(scope, transition_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_records_confidence ON memory_records(confidence DESC)",
    "CREATE INDEX IF NOT EXISTS idx_records_scope_key ON memory_records(scope, type, key)",
    "CREATE INDEX IF NOT EXISTS idx_records_scope_tier_updated ON memory_records(scope, tier, updated_at DESC)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_records_scope_type_key_active
    ON memory_records(scope, type, key)
    WHERE key IS NOT NULL AND is_deleted = FALSE AND superseded_by_id IS NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_records_scope_type_updated ON memory_records(scope, type, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_records_search_gin ON memory_records USING GIN (search_vector)",
)


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    apply_ddl_statements(POSTGRES_DDL if bind.dialect.name == "postgresql" else DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "memory_records",
            "memory_entities",
            "memory_candidates",
            "memory_tier_transitions",
            "memory_fts",
            "migrations",
            "om_meta",
        ),
        index_names=(
            "idx_candidates_scope_status",
            "idx_candidates_session_status",
            "idx_entities_entity",
            "idx_entities_scope_entity",
            "idx_memory_tier_transitions_record",
            "idx_memory_tier_transitions_scope",
            "idx_records_confidence",
            "idx_records_scope_key",
            "idx_records_scope_tier_updated",
            "idx_records_scope_type_key_active",
            "idx_records_scope_type_updated",
        ),
    )
