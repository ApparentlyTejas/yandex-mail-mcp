"""Read-only-first Yandex IMAP interface.

Always use BODY.PEEK[] so inspection does not mark mail as read.
No delete, expunge, SMTP, or arbitrary folder mutation is exposed.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Iterator

from imapclient import IMAPClient

from .config import Settings


class _TextFromHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fragments: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.fragments.append(data)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeError, LookupError):
        return str(value)


def parse_message(raw: bytes, uid: int, *, max_body_chars: int = 4000) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
            if not isinstance(content, str):
                continue
        except (UnicodeError, LookupError, ValueError, TypeError):
            continue
        if part.get_content_type() == "text/plain":
            text_parts.append(content)
        else:
            html_parts.append(content)
    if text_parts:
        body = "\n".join(text_parts)
    else:
        parser = _TextFromHTML()
        parser.feed("\n".join(html_parts))
        body = " ".join(parser.fragments)
    body = re.sub(r"\s+", " ", body).strip()
    return {
        "uid": uid,
        "message_id": str(msg.get("Message-ID", "")),
        "subject": _decode(msg.get("Subject")),
        "from": _decode(msg.get("From")),
        "to": _decode(msg.get("To")),
        "date": str(msg.get("Date", "")),
        "preview": body[:min(max_body_chars, 10000)],
        "has_attachments": any(p.get_content_disposition() == "attachment" for p in msg.walk()),
    }


def valid_uid(uid: int) -> int:
    if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
        raise ValueError("uid must be a positive integer")
    return uid


class Mailbox:
    def __init__(self, config: Settings):
        self.config = config

    @contextmanager
    def connect(self) -> Iterator[IMAPClient]:
        with IMAPClient(
            self.config.imap_host,
            port=self.config.imap_port,
            ssl=True,
            timeout=30,
        ) as imap:
            imap.login(self.config.email, self.config.app_password)
            yield imap

    @staticmethod
    def _fetch(imap: IMAPClient, uids: list[int], max_body_chars: int = 4000) -> list[dict]:
        if not uids:
            return []
        # IMAP BODY.PEEK[] avoids changing the Seen flag.
        fetched = imap.fetch(uids, ["BODY.PEEK[]"])
        result = []
        for uid in uids:
            item = fetched.get(uid, {})
            raw = item.get(b"BODY[]") or item.get(b"BODY.PEEK[]")
            if isinstance(raw, bytes):
                result.append(parse_message(raw, uid, max_body_chars=max_body_chars))
        return result

    def folders(self) -> list[str]:
        with self.connect() as imap:
            return [str(name) for _, _, name in imap.list_folders()]

    def recent(self, limit: int = 20, unread_only: bool = False, folder: str = "INBOX") -> list[dict]:
        limit = max(1, min(limit, 100))
        with self.connect() as imap:
            imap.select_folder(folder, readonly=True)
            uids = imap.search(["UNSEEN"] if unread_only else ["ALL"])
            return self._fetch(imap, sorted(uids)[-limit:][::-1])

    def search(self, query: str, limit: int = 20, folder: str = "INBOX") -> list[dict]:
        """Case-insensitive search of sender, subject and preview in the 300 most recent emails."""
        if not query.strip():
            raise ValueError("query cannot be empty")
        limit = max(1, min(limit, 100))
        needle = query.casefold()
        with self.connect() as imap:
            imap.select_folder(folder, readonly=True)
            uids = sorted(imap.search(["ALL"]))[-300:][::-1]
            found: list[dict] = []
            for start in range(0, len(uids), 30):
                for message in self._fetch(imap, uids[start : start + 30], max_body_chars=2000):
                    if needle in " ".join(
                        [message["from"], message["subject"], message["preview"]]
                    ).casefold():
                        found.append(message)
                        if len(found) >= limit:
                            return found
            return found

    def message(self, uid: int, folder: str = "INBOX") -> dict:
        valid_uid(uid)
        with self.connect() as imap:
            imap.select_folder(folder, readonly=True)
            items = self._fetch(imap, [uid], max_body_chars=10000)
            if not items:
                raise LookupError(f"Message UID {uid} not found in {folder}")
            return items[0]

    def move_to_promotions(self, uid: int, confirm: bool = False) -> dict:
        valid_uid(uid)
        if not self.config.allow_write:
            raise PermissionError("Write operations are disabled. Set YANDEX_ALLOW_WRITE=true")
        if confirm is not True:
            raise PermissionError("Explicit confirm=true is required to move a message")
        folder = self.config.promotions_folder
        if not folder or folder.upper() in {"INBOX", "TRASH", "SPAM", "DELETED", "JUNK"}:
            raise ValueError("Choose a safe dedicated promotions folder")
        with self.connect() as imap:
            existing = {name for _, _, name in imap.list_folders()}
            if folder not in existing:
                imap.create_folder(folder)
            imap.select_folder("INBOX", readonly=False)
            if uid not in imap.search(["UID", str(uid)]):
                raise LookupError(f"Message UID {uid} not found in INBOX")
            imap.move([uid], folder)
        return {"uid": uid, "moved_to": folder, "deleted_permanently": False}
