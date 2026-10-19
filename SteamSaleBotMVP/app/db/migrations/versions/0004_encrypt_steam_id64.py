"""widen users.steam_id64 for encrypted storage

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # steam_id64 теперь хранится зашифрованным (app/services/crypto.py) — шифротекст (Fernet)
    # заметно длиннее исходных 17 цифр, String(32) уже недостаточно.
    op.alter_column("users", "steam_id64", type_=sa.Text(), existing_type=sa.String(length=32))


def downgrade() -> None:
    # ВНИМАНИЕ: если к этому моменту в колонке уже есть зашифрованные значения длиннее 32
    # символов, обратное сужение до String(32) их обрежет/сломает. Проект ещё не в проде —
    # для него это не проблема, но перед реальным откатом на боевых данных стоит сначала
    # вручную расшифровать и решить, что делать со значениями.
    op.alter_column("users", "steam_id64", type_=sa.String(length=32), existing_type=sa.Text())
