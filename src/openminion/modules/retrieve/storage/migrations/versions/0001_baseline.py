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
    CREATE TABLE IF NOT EXISTS retrievectl_docs(
        doc_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        scope TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        corpus_id TEXT,
        scope_key TEXT NOT NULL DEFAULT 'global:legacy'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_raptor_nodes(
        node_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        parent_id TEXT NULL,
        level_int INTEGER NOT NULL,
        summary_text_ref TEXT NOT NULL,
        leaf_unit_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_runs(
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        query TEXT NOT NULL,
        strategy TEXT NOT NULL,
        k INTEGER NOT NULL,
        selected_unit_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_units(
        unit_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        unit_kind TEXT NOT NULL,
        level TEXT NULL,
        node_id TEXT NULL,
        text_ref TEXT NOT NULL,
        context_text_ref TEXT NULL,
        fts_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        token_count INTEGER NOT NULL DEFAULT 0,
        group_id TEXT NULL,
        offsets_json TEXT NOT NULL DEFAULT '{}',
        hit_count INTEGER NOT NULL DEFAULT 0,
        last_hit_at TEXT,
        feedback_score REAL NOT NULL DEFAULT 0.0,
        FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS retrievectl_units_fts
    USING fts5(unit_id UNINDEXED, title, fts_text, tags)
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope ON retrievectl_docs(scope)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope_key ON retrievectl_docs(scope_key)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_source_type ON retrievectl_docs(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_doc_id ON retrievectl_units(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_group ON retrievectl_units(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_kind ON retrievectl_units(unit_kind)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_last_hit_at ON retrievectl_units(last_hit_at)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_node ON retrievectl_units(node_id)",
)

POSTGRES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS retrievectl_docs(
        doc_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        scope TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        corpus_id TEXT,
        scope_key TEXT NOT NULL DEFAULT 'global:legacy'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_raptor_nodes(
        node_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        parent_id TEXT NULL,
        level_int INTEGER NOT NULL,
        summary_text_ref TEXT NOT NULL,
        leaf_unit_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_runs(
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        query TEXT NOT NULL,
        strategy TEXT NOT NULL,
        k INTEGER NOT NULL,
        selected_unit_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_units(
        unit_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        unit_kind TEXT NOT NULL,
        level TEXT NULL,
        node_id TEXT NULL,
        text_ref TEXT NOT NULL,
        context_text_ref TEXT NULL,
        fts_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        token_count INTEGER NOT NULL DEFAULT 0,
        group_id TEXT NULL,
        offsets_json TEXT NOT NULL DEFAULT '{}',
        hit_count INTEGER NOT NULL DEFAULT 0,
        last_hit_at TEXT,
        feedback_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        FOREIGN KEY(doc_id) REFERENCES retrievectl_docs(doc_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrievectl_units_fts(
        unit_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        fts_text TEXT NOT NULL,
        tags TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope ON retrievectl_docs(scope)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_scope_key ON retrievectl_docs(scope_key)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_docs_source_type ON retrievectl_docs(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_doc_id ON retrievectl_units(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_group ON retrievectl_units(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_kind ON retrievectl_units(unit_kind)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_last_hit_at ON retrievectl_units(last_hit_at)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_node ON retrievectl_units(node_id)",
    "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_fts_text ON retrievectl_units_fts(fts_text)",
)


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    apply_ddl_statements(POSTGRES_DDL if bind.dialect.name == "postgresql" else DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "retrievectl_docs",
            "retrievectl_raptor_nodes",
            "retrievectl_runs",
            "retrievectl_units",
            "retrievectl_units_fts",
            "om_meta",
        ),
        index_names=(
            "idx_retrievectl_docs_scope",
            "idx_retrievectl_docs_scope_key",
            "idx_retrievectl_docs_source_type",
            "idx_retrievectl_units_doc_id",
            "idx_retrievectl_units_group",
            "idx_retrievectl_units_kind",
            "idx_retrievectl_units_last_hit_at",
            "idx_retrievectl_units_node",
        ),
    )
