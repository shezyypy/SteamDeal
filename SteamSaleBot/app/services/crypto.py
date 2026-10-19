from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class EncryptionNotConfigured(Exception):
    """DATA_ENCRYPTION_KEY не задан в .env — шифрование персональных данных недоступно."""


def _get_fernet() -> Fernet:
    if not settings.data_encryption_key:
        raise EncryptionNotConfigured(
            "DATA_ENCRYPTION_KEY не задан в .env. Сгенерировать: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.data_encryption_key.encode())


def encrypt_str(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_str(token: str) -> str | None:
    """None, если токен не расшифровывается текущим ключом (например, ключ сменили) —
    лучше молча не отдать битые данные наружу, чем уронить бота посреди работы."""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
