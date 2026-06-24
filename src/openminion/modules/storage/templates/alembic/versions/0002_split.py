from alembic import op
import sqlalchemy as sa


# Example values only; replace in real module revisions.
revision = "0002_split"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_event_payloads",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["session_events.event_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_session_event_payloads_session_id_created",
        "session_event_payloads",
        ["session_id", "created_at"],
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO session_event_payloads(event_id, session_id, payload_json, created_at)
            SELECT event_id, session_id, payload_json, created_at
            FROM session_events
            WHERE payload_json IS NOT NULL
            """
        )
    )

    # SQLite-safe breaking change uses batch mode move-and-copy under the hood.
    with op.batch_alter_table("session_events", recreate="always") as batch_op:
        batch_op.alter_column("event_type", new_column_name="event_name")
        batch_op.drop_column("payload_json")


def downgrade() -> None:
    with op.batch_alter_table("session_events", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("payload_json", sa.Text(), nullable=True))
        batch_op.alter_column("event_name", new_column_name="event_type")

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE session_events
            SET payload_json = (
                SELECT p.payload_json
                FROM session_event_payloads AS p
                WHERE p.event_id = session_events.event_id
            )
            """
        )
    )

    op.drop_index(
        "ix_session_event_payloads_session_id_created",
        table_name="session_event_payloads",
    )
    op.drop_table("session_event_payloads")
