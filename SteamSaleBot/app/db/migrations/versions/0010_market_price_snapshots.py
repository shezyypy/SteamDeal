"""durable Market price history

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("market_hash_name", sa.String(length=256), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_market_price_snapshots_appid", "market_price_snapshots", ["appid"])
    op.create_index("ix_market_price_snapshots_market_hash_name", "market_price_snapshots", ["market_hash_name"])
    op.create_index("ix_market_price_snapshots_observed_at", "market_price_snapshots", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_market_price_snapshots_observed_at", table_name="market_price_snapshots")
    op.drop_index("ix_market_price_snapshots_market_hash_name", table_name="market_price_snapshots")
    op.drop_index("ix_market_price_snapshots_appid", table_name="market_price_snapshots")
    op.drop_table("market_price_snapshots")
