from email.message import EmailMessage

import pytest

from yandex_mail_mcp.config import Settings
from yandex_mail_mcp.mail import Mailbox, parse_message, valid_uid


def test_parse_message_decodes_unicode_and_does_not_include_attachment():
    msg = EmailMessage()
    msg["From"] = "Test Sender <test@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = "Важный документ"
    msg.set_content("Здравствуйте! Оплатите счёт до пятницы.")
    msg.add_attachment(b"SECRET-ATTACHMENT", maintype="application", subtype="octet-stream", filename="test.bin")
    parsed = parse_message(msg.as_bytes(), 123)
    assert parsed["subject"] == "Важный документ"
    assert "Оплатите" in parsed["preview"]
    assert "SECRET-ATTACHMENT" not in parsed["preview"]
    assert parsed["has_attachments"] is True
    assert parsed["uid"] == 123


def test_html_ignores_script_content():
    msg = EmailMessage()
    msg["Subject"] = "HTML"
    msg.set_content("<html><script>do something bad</script><body>Hello world</body></html>", subtype="html")
    parsed = parse_message(msg.as_bytes(), 1)
    assert "Hello world" in parsed["preview"]
    assert "something bad" not in parsed["preview"]


def test_invalid_uid_rejected():
    for uid in (0, -1, True, "1"):
        with pytest.raises(ValueError):
            valid_uid(uid)


def test_no_mutation_without_explicit_opt_in():
    box = Mailbox(Settings(email="test@yandex.com", app_password="not-a-real-password"))
    with pytest.raises(PermissionError, match="disabled"):
        box.move_to_promotions(3, confirm=True)


def test_no_mutation_without_confirmation():
    box = Mailbox(Settings(email="test@yandex.com", app_password="not-a-real-password", allow_write=True))
    with pytest.raises(PermissionError, match="confirm"):
        box.move_to_promotions(3)
