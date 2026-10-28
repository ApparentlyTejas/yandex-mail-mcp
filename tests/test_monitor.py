import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from yandex_mail_mcp.monitor import classify, open_state, save_progress, poll_once
from yandex_mail_mcp.mail import Mailbox


class FakeResponse:
    def __init__(self, value):
        self.output_text = json.dumps(value)


class FakeResponses:
    def __init__(self, value):
        self.value = value

    def create(self, **kwargs):
        return FakeResponse(self.value)


class FakeClient:
    def __init__(self, value):
        self.responses = FakeResponses(value)


MESSAGE = {"from": "sender@example.com", "subject": "Счёт", "date": "", "preview": "Invoice"}


def test_valid_classification():
    result = classify(
        FakeClient({"category": "IMPORTANT", "urgent": True, "confidence": 0.95, "reason": "Платёж"}),
        "fake", MESSAGE,
    )
    assert result["category"] == "IMPORTANT"
    assert result["urgent"]


def test_invalid_category_rejected():
    with pytest.raises(ValueError, match="category"):
        classify(
            FakeClient({"category": "DELETE", "urgent": False, "confidence": 1, "reason": "x"}),
            "fake", MESSAGE,
        )


def test_promotions_never_urgent():
    result = classify(
        FakeClient({"category": "PROMOTIONS", "urgent": True, "confidence": 0.99, "reason": "Реклама"}),
        "fake", MESSAGE,
    )
    assert result["urgent"] is False


def test_state_progress(tmp_path):
    connection = open_state(str(tmp_path / "test.sqlite3"))
    save_progress(connection, "INBOX", "7", 42)
    save_progress(connection, "INBOX", "7", 43)
    assert connection.execute("SELECT last_uid FROM mailbox_progress").fetchone()[0] == 43
    connection.close()


def test_bad_message_is_skipped_and_later_message_processed(tmp_path, monkeypatch):
    conn = open_state(str(tmp_path / "state.sqlite"))
    save_progress(conn, "INBOX", "7", 0)

    class IMAP:
        def select_folder(self, *args, **kwargs):
            return {b"UIDVALIDITY": 7}

        def search(self, criteria):
            return [1, 2]

    class Box:
        config = SimpleNamespace(allow_write=False)

        @contextmanager
        def connect(self):
            yield IMAP()

    monkeypatch.setattr(Mailbox, "_fetch", lambda *args, **kwargs: [dict(MESSAGE, uid=1), dict(MESSAGE, uid=2)])
    calls = []

    def classifier(ai, model, message):
        calls.append(message["uid"])
        if message["uid"] == 1:
            raise ValueError("bad response")
        return {"category": "OTHER", "urgent": False, "confidence": 0.9, "reason": "ok"}

    monkeypatch.setattr("yandex_mail_mcp.monitor.classify", classifier)
    for _ in range(3):
        poll_once(Box(), conn, None, "test")
    assert calls == [1, 1, 1, 2]
    assert conn.execute("SELECT last_uid FROM mailbox_progress").fetchone() == (2,)
    assert conn.execute("SELECT attempts, stage FROM processing_failures").fetchone() == (3, "classification")
    conn.close()


def test_bad_state_path_has_actionable_error(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a directory")
    with pytest.raises(RuntimeError, match="STATE_DB"):
        open_state(str(path / "state.sqlite"))


def test_notification_retry_reuses_saved_classification(tmp_path, monkeypatch):
    conn = open_state(str(tmp_path / "state.sqlite"))
    save_progress(conn, "INBOX", "7", 0)

    class IMAP:
        def select_folder(self, *args, **kwargs):
            return {b"UIDVALIDITY": 7}

        def search(self, criteria):
            return [1]

    class Box:
        config = SimpleNamespace(allow_write=False)

        @contextmanager
        def connect(self):
            yield IMAP()

    monkeypatch.setattr(Mailbox, "_fetch", lambda *args, **kwargs: [dict(MESSAGE, uid=1)])
    calls = []
    def classifier(*args):
        calls.append(1)
        return {"category": "IMPORTANT", "urgent": True, "confidence": 0.9, "reason": "ok"}

    monkeypatch.setattr("yandex_mail_mcp.monitor.classify", classifier)
    monkeypatch.setattr("yandex_mail_mcp.monitor.notify_telegram", lambda *args: (_ for _ in ()).throw(RuntimeError("failed")))
    poll_once(Box(), conn, None, "test", "token", "chat")
    monkeypatch.setattr("yandex_mail_mcp.monitor.notify_telegram", lambda *args: None)
    poll_once(Box(), conn, None, "test", "token", "chat")
    assert calls == [1]
    assert conn.execute("SELECT last_uid FROM mailbox_progress").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM processing_failures").fetchone() == (0,)
    conn.close()
