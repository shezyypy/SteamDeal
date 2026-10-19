"""track Stars refunds and entitlement boundaries

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("premium_until_before", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("premium_until_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "refunded_at")
    op.drop_column("payments", "premium_until_after")
    op.drop_column("payments", "premium_until_before")
