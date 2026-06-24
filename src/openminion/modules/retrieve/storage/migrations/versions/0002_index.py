revision = "0002_index"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS retrievectl_units_fts_rebuild")
        op.execute(
            """
            CREATE VIRTUAL TABLE retrievectl_units_fts_rebuild
            USING fts5(unit_id UNINDEXED, title, fts_text, tags)
            """
        )
        op.execute(
            """
            INSERT INTO retrievectl_units_fts_rebuild(unit_id, title, fts_text, tags)
            SELECT
                f.unit_id,
                COALESCE(d.title, ''),
                f.fts_text,
                f.tags
            FROM retrievectl_units_fts f
            JOIN retrievectl_units u ON u.unit_id = f.unit_id
            JOIN retrievectl_docs d ON d.doc_id = u.doc_id
            """
        )
        op.execute("DROP TABLE retrievectl_units_fts")
        op.execute(
            "ALTER TABLE retrievectl_units_fts_rebuild RENAME TO retrievectl_units_fts"
        )
        return

    op.execute(
        """
        ALTER TABLE retrievectl_units_fts
        ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute(
        """
        UPDATE retrievectl_units_fts f
        SET title = d.title
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.unit_id = f.unit_id
        """
    )


def downgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS retrievectl_units_fts_rebuild")
        op.execute(
            """
            CREATE VIRTUAL TABLE retrievectl_units_fts_rebuild
            USING fts5(unit_id UNINDEXED, fts_text, tags)
            """
        )
        op.execute(
            """
            INSERT INTO retrievectl_units_fts_rebuild(unit_id, fts_text, tags)
            SELECT unit_id, fts_text, tags
            FROM retrievectl_units_fts
            """
        )
        op.execute("DROP TABLE retrievectl_units_fts")
        op.execute(
            "ALTER TABLE retrievectl_units_fts_rebuild RENAME TO retrievectl_units_fts"
        )
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS retrievectl_units_fts_rebuild(
            unit_id TEXT PRIMARY KEY,
            fts_text TEXT NOT NULL,
            tags TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO retrievectl_units_fts_rebuild(unit_id, fts_text, tags)
        SELECT unit_id, fts_text, tags
        FROM retrievectl_units_fts
        ON CONFLICT (unit_id) DO UPDATE SET
            fts_text = EXCLUDED.fts_text,
            tags = EXCLUDED.tags
        """
    )
    op.execute("DROP TABLE retrievectl_units_fts")
    op.execute(
        "ALTER TABLE retrievectl_units_fts_rebuild RENAME TO retrievectl_units_fts"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_retrievectl_units_fts_text ON retrievectl_units_fts(fts_text)"
    )
