from email.message import EmailMessage
from contextlib import contextmanager

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


def test_system_folder_flag_is_rejected():
    box = Mailbox(Settings(email="test@yandex.com", app_password="x", allow_write=True, promotions_folder="Archive"))
    class IMAP:
        def list_folders(self): return [((b"\\Trash",), b"/", "Archive")]
    @contextmanager
    def connect(): yield IMAP()
    box.connect = connect
    with pytest.raises(ValueError, match="safe"):
        box.move_to_promotions(3, confirm=True)


@pytest.mark.parametrize("name", ["Черновики", "Удалённые", "Sent", "Spam"])
def test_system_folder_name_is_rejected(name):
    box = Mailbox(Settings(email="test@yandex.com", app_password="x", allow_write=True, promotions_folder=name))
    with pytest.raises(ValueError, match="safe"):
        box.move_to_promotions(3, confirm=True)


def test_preview_fetches_only_bounded_text_section():
    calls = []
    class IMAP:
        def fetch(self, uids, fields):
            calls.append(fields)
            if fields == ["BODY.PEEK[HEADER]", "BODYSTRUCTURE"]:
                structure = ([
                    (b"TEXT", b"PLAIN", (b"CHARSET", b"UTF-8"), None, None, b"BASE64", 12, 1, None, None),
                    (b"APPLICATION", b"PDF", (b"NAME", b"large.pdf"), None, None, b"BASE64", 5000000, None, (b"ATTACHMENT", (b"FILENAME", b"large.pdf"))),
                ], b"MIXED")
                return {1: {b"BODY[HEADER]": b"Subject: Hello\r\nFrom: a@example.com\r\n\r\n", b"BODYSTRUCTURE": structure}}
            return {1: {b"BODY[1]<0>": b"SGVsbG8gd29ybGQ="}}
    result = Mailbox._fetch(IMAP(), [1])
    assert result[0]["preview"] == "Hello world"
    assert result[0]["has_attachments"]
    assert calls == [["BODY.PEEK[HEADER]", "BODYSTRUCTURE"], ["BODY.PEEK[1]<0.16384>"]]


def test_get_email_rejects_large_message_before_body_fetch():
    box = Mailbox(Settings(email="test@yandex.com", app_password="x"))
    calls = []
    class IMAP:
        def select_folder(self, *args, **kwargs): pass
        def fetch(self, uids, fields):
            calls.append(fields)
            return {1: {b"RFC822.SIZE": 21 * 1024 * 1024}}
    @contextmanager
    def connect(): yield IMAP()
    box.connect = connect
    with pytest.raises(ValueError, match="20 MiB"):
        box.message(1)
    assert calls == [["RFC822.SIZE"]]


def test_search_uses_server_results_without_300_message_cap(monkeypatch):
    box = Mailbox(Settings(email="test@yandex.com", app_password="x"))
    class IMAP:
        def select_folder(self, *args, **kwargs): pass
        def search(self, criteria, charset=None):
            assert criteria == ["OR", "OR", "FROM", "счёт", "SUBJECT", "счёт", "TEXT", "счёт"]
            assert charset == "UTF-8"
            return [1, 500]
    @contextmanager
    def connect(): yield IMAP()
    box.connect = connect
    monkeypatch.setattr(Mailbox, "_fetch", staticmethod(lambda imap, uids, **kwargs: [{"uid": uid} for uid in uids]))
    assert box.search("счёт", limit=1) == [{"uid": 500}]
