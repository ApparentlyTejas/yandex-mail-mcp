from dataclasses import asdict

from pydantic import SecretStr

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
