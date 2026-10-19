"""trial premium, digest opt-in, referral program

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-25

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("trial_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "chats",
        sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("chats", sa.Column("referred_by", sa.BigInteger(), nullable=True))
    op.add_column(
        "chats",
        sa.Column("referral_rewarded", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_foreign_key(
        "fk_chats_referred_by", "chats", "chats", ["referred_by"], ["chat_id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_chats_referred_by", "chats", type_="foreignkey")
    op.drop_column("chats", "referral_rewarded")
    op.drop_column("chats", "referred_by")
    op.drop_column("chats", "digest_enabled")
    op.drop_column("chats", "trial_used_at")
