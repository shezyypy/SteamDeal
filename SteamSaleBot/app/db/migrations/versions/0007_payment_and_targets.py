"""idempotent Stars payments and tracked-game target prices

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_games", sa.Column("target_cents", sa.Integer(), nullable=True))
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(length=128), nullable=False),
        sa.Column("provider_payment_charge_id", sa.String(length=128), nullable=True),
        sa.Column("invoice_payload", sa.String(length=256), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("premium_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("telegram_payment_charge_id", name="uq_payment_telegram_charge"),
    )


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_column("tracked_games", "target_cents")
