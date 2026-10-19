import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.services import crypto


@pytest.fixture(autouse=True)
def _with_encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "data_encryption_key", Fernet.generate_key().decode())
    yield


def test_encrypt_then_decrypt_roundtrip():
    token = crypto.encrypt_str("76561197960287930")
    assert token != "76561197960287930"  # реально зашифровано, не просто скопировано
    assert crypto.decrypt_str(token) == "76561197960287930"


def test_decrypt_returns_none_for_garbage_token():
    assert crypto.decrypt_str("not-a-valid-fernet-token") is None


def test_decrypt_returns_none_when_key_rotated():
    token = crypto.encrypt_str("76561197960287930")
    from app.config import settings as live_settings

    live_settings.data_encryption_key = Fernet.generate_key().decode()
    assert crypto.decrypt_str(token) is None


def test_encrypt_raises_clear_error_without_key(monkeypatch):
    monkeypatch.setattr(settings, "data_encryption_key", None)
    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.encrypt_str("76561197960287930")
