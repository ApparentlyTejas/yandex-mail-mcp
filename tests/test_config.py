from yandex_mail_mcp.config import Settings


def test_settings_repr_does_not_expose_password():
    settings = Settings(email="test@yandex.com", app_password="SUPERSECRET")

    assert "SUPERSECRET" not in repr(settings)
    assert "test@yandex.com" in repr(settings)
