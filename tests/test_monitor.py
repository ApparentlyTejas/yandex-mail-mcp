import json
import sqlite3
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from yandex_mail_mcp.monitor import classify, notify_telegram, open_state, save_progress, poll_once
from yandex_mail_mcp.mail import Mailbox


class FakeResponse:
    def __init__(self, value):
        self.output_text = json.dumps(value)


class FakeResponses:
    def __init__(self, value):
        self.value = value

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse(self.value)


class FakeClient:
    def __init__(self, value):
        self.responses = FakeResponses(value)


MESSAGE = {"from": "sender@example.com", "subject": "Счёт", "date": "", "preview": "Invoice"}


def test_valid_classification():
    client = FakeClient(
        {"category": "IMPORTANT", "urgent": True, "confidence": 0.95, "reason": "Платёж"}
    )
    result = classify(
        client,
        "fake",
        MESSAGE,
    )
    assert result["category"] == "IMPORTANT"
    assert result["urgent"]
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["text"]["format"]["strict"] is True


def test_incomplete_classification_is_rejected():
    class Responses:
        def create(self, **kwargs):
            return SimpleNamespace(status="incomplete", output_text="{}")

    with pytest.raises(ValueError, match="incomplete"):
        classify(SimpleNamespace(responses=Responses()), "fake", MESSAGE)


def test_invalid_category_rejected():
    with pytest.raises(ValueError, match="category"):
        classify(
            FakeClient({"category": "DELETE", "urgent": False, "confidence": 1, "reason": "x"}),
            "fake",
            MESSAGE,
        )


def test_telegram_notification_formats_message_and_checks_response(monkeypatch):
    captured = []

    def urlopen(request, timeout):
        captured.append((request, timeout))
        return BytesIO(b'{"ok": true}')

    monkeypatch.setattr("yandex_mail_mcp.monitor.urlopen", urlopen)
    decision = {"urgent": True, "reason": "Оплата"}
    notify_telegram("test-token", "42", MESSAGE, decision)
    request, timeout = captured[0]
    data = parse_qs(request.data.decode())
    assert timeout == 15
    assert data["chat_id"] == ["42"]
    assert "Счёт" in data["text"][0]
    assert "Оплата" in data["text"][0]
    monkeypatch.setattr(
        "yandex_mail_mcp.monitor.urlopen", lambda *args, **kwargs: BytesIO(b'{"ok": false}')
    )
    with pytest.raises(RuntimeError, match="non-success"):
        notify_telegram("test-token", "42", MESSAGE, decision)


def test_promotions_never_urgent():
    result = classify(
        FakeClient(
            {"category": "PROMOTIONS", "urgent": True, "confidence": 0.99, "reason": "Реклама"}
        ),
        "fake",
        MESSAGE,
    )
    assert result["urgent"] is False


def test_state_progress(tmp_path):
    connection = open_state(str(tmp_path / "test.sqlite3"))
    save_progress(connection, "INBOX", "7", 42)
    save_progress(connection, "INBOX", "7", 43)
    assert connection.execute("SELECT last_uid FROM mailbox_progress").fetchone()[0] == 43
    connection.close()


def test_uidvalidity_change_discards_pending_with_warning(tmp_path, caplog):
    conn = open_state(str(tmp_path / "state.sqlite"))
    save_progress(conn, "INBOX", "old", 5)
    conn.execute("INSERT INTO pending_messages VALUES ('old', 5)")
    conn.commit()

    class IMAP:
        def select_folder(self, *args, **kwargs):
            return {b"UIDVALIDITY": 7, b"UIDNEXT": 11}

    class Box:
        @contextmanager
        def connect(self):
            yield IMAP()

    poll_once(Box(), conn, None, "fake")
    assert conn.execute("SELECT uidvalidity, last_uid FROM mailbox_progress").fetchone() == (
        "7",
        10,
    )
    assert conn.execute("SELECT count(*) FROM pending_messages").fetchone() == (0,)
    assert "UIDVALIDITY changed" in caplog.text
    conn.close()


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

    monkeypatch.setattr(
        Mailbox, "_fetch", lambda *args, **kwargs: [dict(MESSAGE, uid=1), dict(MESSAGE, uid=2)]
    )
    calls = []

    def classifier(ai, model, message):
        calls.append(message["uid"])
        if message["uid"] == 1:
            raise ValueError("bad response")
        return {"category": "OTHER", "urgent": False, "confidence": 0.9, "reason": "ok"}

    monkeypatch.setattr("yandex_mail_mcp.monitor.classify", classifier)
    for _ in range(3):
        poll_once(Box(), conn, None, "test")
    assert calls == [1, 2, 1, 1]
    assert conn.execute("SELECT last_uid FROM mailbox_progress").fetchone() == (2,)
    assert conn.execute("SELECT attempts, stage FROM processing_failures").fetchone() == (
        3,
        "classification",
    )
    conn.close()


def test_bad_state_path_has_actionable_error(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a directory")
    with pytest.raises(RuntimeError, match="STATE_DB"):
        open_state(str(path / "state.sqlite"))


def test_state_schema_failure_closes_connection(tmp_path, monkeypatch):
    class BrokenConnection:
        closed = False

        def execute(self, sql):
            raise sqlite3.OperationalError("readonly database")

        def close(self):
            self.closed = True

    conn = BrokenConnection()
    monkeypatch.setattr("yandex_mail_mcp.monitor.sqlite3.connect", lambda path: conn)
    with pytest.raises(RuntimeError, match="STATE_DB"):
        open_state(str(tmp_path / "state.sqlite"))
    assert conn.closed


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
    monkeypatch.setattr(
        "yandex_mail_mcp.monitor.notify_telegram",
        lambda *args: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    poll_once(Box(), conn, None, "test", "token", "chat")
    monkeypatch.setattr("yandex_mail_mcp.monitor.notify_telegram", lambda *args: None)
    poll_once(Box(), conn, None, "test", "token", "chat")
    assert calls == [1]
    assert conn.execute("SELECT last_uid FROM mailbox_progress").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM processing_failures").fetchone() == (0,)
    conn.close()


def test_attempts_reset_when_stage_changes_and_queue_survives_restart(tmp_path, monkeypatch):
    path = str(tmp_path / "state.sqlite")
    conn = open_state(path)
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
        if len(calls) <= 2:
            raise ValueError("bad response")
        return {"category": "IMPORTANT", "urgent": True, "confidence": 0.9, "reason": "ok"}

    monkeypatch.setattr("yandex_mail_mcp.monitor.classify", classifier)
    monkeypatch.setattr(
        "yandex_mail_mcp.monitor.notify_telegram",
        lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    poll_once(Box(), conn, None, "test", "token", "chat")
    conn.close()
    conn = open_state(path)
    poll_once(Box(), conn, None, "test", "token", "chat")
    poll_once(Box(), conn, None, "test", "token", "chat")
    assert conn.execute("SELECT attempts, stage FROM processing_failures").fetchone() == (
        1,
        "notification",
    )
    assert conn.execute("SELECT uid FROM pending_messages").fetchone() == (1,)
    monkeypatch.setattr("yandex_mail_mcp.monitor.notify_telegram", lambda *args: None)
    poll_once(Box(), conn, None, "test", "token", "chat")
    assert len(calls) == 3
    assert conn.execute("SELECT count(*) FROM pending_messages").fetchone() == (0,)
    conn.close()


def test_removed_pending_message_clears_retry_record(tmp_path, monkeypatch):
    conn = open_state(str(tmp_path / "state.sqlite"))
    save_progress(conn, "INBOX", "7", 1)
    conn.execute("INSERT INTO pending_messages VALUES ('7', 1)")
    conn.execute("INSERT INTO processing_failures VALUES ('7', 1, 1, 'classification')")
    conn.commit()

    class IMAP:
        def select_folder(self, *args, **kwargs):
            return {b"UIDVALIDITY": 7}

        def search(self, criteria):
            return []

    class Box:
        @contextmanager
        def connect(self):
            yield IMAP()

    monkeypatch.setattr(Mailbox, "_fetch", lambda *args, **kwargs: [])
    poll_once(Box(), conn, None, "test")
    assert conn.execute("SELECT count(*) FROM pending_messages").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM processing_failures").fetchone() == (0,)
    conn.close()


def test_incremental_search_and_uidnext_baseline(tmp_path, monkeypatch):
    conn = open_state(str(tmp_path / "state.sqlite"))
    searches = []

    class IMAP:
        def select_folder(self, *args, **kwargs):
            return {b"UIDVALIDITY": 7, b"UIDNEXT": 101}

        def search(self, criteria):
            searches.append(criteria)
            return [100, 101]

    class Box:
        config = SimpleNamespace(allow_write=False)

        @contextmanager
        def connect(self):
            yield IMAP()

    monkeypatch.setattr(
        Mailbox, "_fetch", lambda imap, uids, **kwargs: [dict(MESSAGE, uid=uid) for uid in uids]
    )
    monkeypatch.setattr(
        "yandex_mail_mcp.monitor.classify",
        lambda *args: {"category": "OTHER", "urgent": False, "confidence": 1, "reason": "ok"},
    )
    poll_once(Box(), conn, None, "test")
    assert not searches
    poll_once(Box(), conn, None, "test")
    assert searches == [["UID", "101:*"]]
    assert conn.execute("SELECT uid FROM decisions").fetchall() == [(101,)]
    conn.close()


def test_stop_keeps_queued_messages_for_next_poll(tmp_path, monkeypatch):
    import threading

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

    monkeypatch.setattr(
        Mailbox, "_fetch", lambda imap, uids, **kwargs: [dict(MESSAGE, uid=uid) for uid in uids]
    )
    stop = threading.Event()

    def classify_one(*args):
        stop.set()
        return {"category": "OTHER", "urgent": False, "confidence": 1, "reason": "ok"}

    monkeypatch.setattr("yandex_mail_mcp.monitor.classify", classify_one)
    poll_once(Box(), conn, None, "test", stop_event=stop)
    assert conn.execute("SELECT uid FROM pending_messages").fetchall() == [(2,)]
    conn.close()


def test_incomplete_fetch_retries_one_uid_without_blocking_another(tmp_path, monkeypatch):
    conn = open_state(str(tmp_path / "state.sqlite"))
    save_progress(conn, "INBOX", "7", 0)

    class IMAP:
        def select_folder(self, *args, **kwargs):
            return {b"UIDVALIDITY": 7}

        def search(self, criteria):
            return [1, 2]

        def fetch(self, uids, fields):
            return {
                1: {b"BODY[HEADER]": b"Subject: incomplete\r\n\r\n"},
                2: {
                    b"BODY[HEADER]": b"Subject: valid\r\n\r\n",
                    b"BODYSTRUCTURE": (
                        b"APPLICATION",
                        b"PDF",
                        None,
                        None,
                        None,
                        b"BASE64",
                        0,
                        None,
                        None,
                    ),
                },
            }

    class Box:
        config = SimpleNamespace(allow_write=False)

        @contextmanager
        def connect(self):
            yield IMAP()

    seen = []

    def classify_valid(ai, model, message):
        seen.append(message["uid"])
        return {"category": "OTHER", "urgent": False, "confidence": 1, "reason": "ok"}

    monkeypatch.setattr("yandex_mail_mcp.monitor.classify", classify_valid)
    for _ in range(3):
        poll_once(Box(), conn, None, "test")
    assert seen == [2]
    assert conn.execute("SELECT uid, attempts, stage FROM processing_failures").fetchall() == [
        (1, 3, "fetch")
    ]
    assert conn.execute("SELECT count(*) FROM pending_messages").fetchone() == (0,)
    conn.close()


def test_second_stop_signal_interrupts():
    import threading
    from yandex_mail_mcp.monitor import _stop_handler

    event = threading.Event()
    stop = _stop_handler(event)
    stop(None, None)
    assert event.is_set()
    with pytest.raises(KeyboardInterrupt):
        stop(None, None)
