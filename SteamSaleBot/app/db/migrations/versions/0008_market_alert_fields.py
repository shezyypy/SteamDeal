"""persist Mini App market alert fields

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_market_items", sa.Column("mode", sa.String(length=10), nullable=False, server_default="price"))
    op.add_column("tracked_market_items", sa.Column("pct", sa.Float(), nullable=True))
    op.add_column("tracked_market_items", sa.Column("base_cents", sa.Integer(), nullable=True))
    op.add_column("tracked_market_items", sa.Column("item_icon", sa.Text(), nullable=True))
    op.add_column("tracked_market_items", sa.Column("item_type", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_market_items", "item_type")
    op.drop_column("tracked_market_items", "item_icon")
    op.drop_column("tracked_market_items", "base_cents")
    op.drop_column("tracked_market_items", "pct")
    op.drop_column("tracked_market_items", "mode")
