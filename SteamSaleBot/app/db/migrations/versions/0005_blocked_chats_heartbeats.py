"""blocked chat tracking, worker heartbeats

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "worker_heartbeats",
        sa.Column("job_name", sa.String(length=64), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_column("chats", "blocked_at")
