import asyncio
import imaplib
import pytest
import anyio.to_thread
from mcp.server.mcpserver.exceptions import ToolError

from yandex_mail_mcp import server
from yandex_mail_mcp.mail import MailValueError


@pytest.fixture(autouse=True)
def run_sync_with_asyncio(monkeypatch):
    # Run synchronous tool bodies inline while testing the SDK error boundary.
    async def run_sync(fn, *args, **kwargs):
        return fn(*args)
    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync)


def test_expected_error_reaches_tool_caller(monkeypatch):
    class Mailbox:
        def search(self, **kwargs):
            raise MailValueError("query cannot be empty")

    monkeypatch.setattr(server, "_mailbox", lambda: Mailbox())
    with pytest.raises(ToolError, match="query cannot be empty"):
        asyncio.run(server.mcp.call_tool("search_emails", {"query": ""}))


def test_unexpected_keyerror_is_not_converted(monkeypatch):
    class Box:
        def folders(self):
            raise KeyError("internal")
    monkeypatch.setattr(server, "_mailbox", lambda: Box())
    with pytest.raises(KeyError):
        server.list_folders()


def test_imap_error_reaches_client(monkeypatch):
    class Box:
        def folders(self):
            raise imaplib.IMAP4.error("No such mailbox")
    monkeypatch.setattr(server, "_mailbox", lambda: Box())
    with pytest.raises(ToolError, match="No such mailbox"):
        asyncio.run(server.mcp.call_tool("list_folders", {}))
