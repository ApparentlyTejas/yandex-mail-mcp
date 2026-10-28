from dataclasses import asdict

from pydantic import SecretStr
import pytest

from yandex_mail_mcp.config import Settings


def test_settings_repr_does_not_expose_password():
    settings = Settings(email="test@yandex.com", app_password=SecretStr("SUPERSECRET"))

    assert "SUPERSECRET" not in repr(settings)
    assert "test@yandex.com" in repr(settings)
    assert "SUPERSECRET" not in str(settings)
    assert "SUPERSECRET" not in f"{settings}"
    assert "SUPERSECRET" not in str(asdict(settings))
    assert "SUPERSECRET" not in str(vars(settings))
    assert settings.app_password.get_secret_value() == "SUPERSECRET"


def test_invalid_port_identifies_variable(monkeypatch):
    monkeypatch.setenv("YANDEX_EMAIL", "a@example.com")
    monkeypatch.setenv("YANDEX_APP_PASSWORD", "SUPERSECRET")
    monkeypatch.setenv("YANDEX_IMAP_PORT", "")
    with pytest.raises(ValueError, match="YANDEX_IMAP_PORT"):
        Settings.from_env()
