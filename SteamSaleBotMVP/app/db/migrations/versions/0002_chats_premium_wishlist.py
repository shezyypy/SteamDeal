"""chats, premium, group tracking support

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_region", sa.String(length=8), nullable=False, server_default="ru"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Пересоздаём tracked_games/notification_log под чаты вместо пользователей — проект ещё
    # не в проде (реальных строк в этих двух таблицах нет), поэтому проще и надёжнее
    # пересоздать, чем городить ALTER TABLE RENAME COLUMN + смену FK на живых данных.
    op.drop_table("notification_log")
    op.drop_table("tracked_games")

    op.create_table(
        "tracked_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("added_by_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("added_by_username", sa.String(length=64), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "appid", name="uq_chat_game"),
    )
    op.create_index("ix_tracked_games_appid", "tracked_games", ["appid"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "appid", "discount_percent", name="uq_notification"),
    )
    op.create_index("ix_notification_log_appid", "notification_log", ["appid"])


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("tracked_games")
    op.drop_table("chats")

    op.create_table(
        "tracked_games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_tg_id", sa.BigInteger(), sa.ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_tg_id", "appid", name="uq_user_game"),
    )
    op.create_index("ix_tracked_games_appid", "tracked_games", ["appid"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_tg_id", sa.BigInteger(), sa.ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("appid", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_tg_id", "appid", "discount_percent", name="uq_notification"),
    )
    op.create_index("ix_notification_log_appid", "notification_log", ["appid"])
