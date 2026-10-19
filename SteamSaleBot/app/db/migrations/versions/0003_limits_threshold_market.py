"""per-chat discount threshold, market item price alerts

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("min_discount_percent", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "tracked_market_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("market_hash_name", sa.String(length=256), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("threshold_cents", sa.Integer(), nullable=False),
        sa.Column("added_by_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("added_by_username", sa.String(length=64), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "market_hash_name", "direction", name="uq_chat_item_direction"),
    )


def downgrade() -> None:
    op.drop_table("tracked_market_items")
    op.drop_column("chats", "min_discount_percent")
