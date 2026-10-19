"""durable Mini App event feed

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "miniapp_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("price", sa.JSON(), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_miniapp_events_chat_id", "miniapp_events", ["chat_id"])
    op.create_index("ix_miniapp_events_created_at", "miniapp_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_miniapp_events_created_at", table_name="miniapp_events")
    op.drop_index("ix_miniapp_events_chat_id", table_name="miniapp_events")
    op.drop_table("miniapp_events")
