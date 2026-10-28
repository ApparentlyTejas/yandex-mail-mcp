import json

import pytest

from yandex_mail_mcp.monitor import classify, open_state, save_progress


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
