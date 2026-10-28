import pytest
from mcp.server.mcpserver.exceptions import ToolError

from yandex_mail_mcp import server


def test_expected_error_reaches_tool_caller(monkeypatch):
    class Mailbox:
        def search(self, **kwargs):
            raise ValueError("query cannot be empty")

    monkeypatch.setattr(server, "_mailbox", lambda: Mailbox())
    with pytest.raises(ToolError, match="query cannot be empty"):
        server.search_emails("")
