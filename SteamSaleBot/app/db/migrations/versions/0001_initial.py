"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("steam_id64", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "tracked_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_tg_id",
            sa.BigInteger(),
            sa.ForeignKey("users.tg_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_tg_id", "appid", name="uq_user_game"),
    )
    op.create_index("ix_tracked_games_appid", "tracked_games", ["appid"])

    op.create_table(
        "sale_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("price_original_cents", sa.Integer(), nullable=False),
        sa.Column("price_final_cents", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), server_default="RUB"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sale_snapshots_appid", "sale_snapshots", ["appid"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_tg_id",
            sa.BigInteger(),
            sa.ForeignKey("users.tg_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_tg_id", "appid", "discount_percent", name="uq_notification"),
    )
    op.create_index("ix_notification_log_appid", "notification_log", ["appid"])


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("sale_snapshots")
    op.drop_table("tracked_games")
    op.drop_table("users")
