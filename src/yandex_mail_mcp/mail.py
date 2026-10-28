"""Read-only-first Yandex IMAP interface.

Always use BODY.PEEK[] so inspection does not mark mail as read.
No delete, expunge, SMTP, or arbitrary folder mutation is exposed.
"""
from __future__ import annotations

import re
import imaplib
from contextlib import contextmanager
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Iterator

from imapclient import IMAPClient

from .config import Settings


class MailError(Exception):
    """An expected error that can be reported to a tool caller."""


class MailValueError(MailError, ValueError):
    pass


class MailLookupError(MailError, LookupError):
    pass


class MailPermissionError(MailError, PermissionError):
    pass


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
        raise MailValueError("uid must be a positive integer")
    return uid


def _text_sections(structure):
    """Return safe text section metadata and whether MIME attachments exist."""
    sections = []
    has_attachments = False

    def string(value, default=""):
        if isinstance(value, bytes):
            return value.decode("ascii", errors="replace")
        return value if isinstance(value, str) else default

    def walk(part, path):
        nonlocal has_attachments
        if not isinstance(part, (tuple, list)) or not part:
            return
        if isinstance(part[0], list):
            for index, child in enumerate(part[0], 1):
                walk(child, f"{path}.{index}" if path else str(index))
            return
        if len(part) < 7:
            return
        kind = string(part[0]).lower()
        subtype = string(part[1]).lower()
        params = part[2] if isinstance(part[2], (tuple, list)) else ()
        params = {string(params[i]).lower(): string(params[i + 1]) for i in range(0, len(params) - 1, 2)}
        disposition_index = 9 if kind == "text" else 8
        disposition = part[disposition_index] if len(part) > disposition_index else None
        disposition_name = string(disposition[0]).lower() if isinstance(disposition, (tuple, list)) and disposition else ""
        disposition_params = disposition[1] if isinstance(disposition, (tuple, list)) and len(disposition) > 1 else None
        is_attachment = disposition_name == "attachment" or bool(params.get("name")) or bool(disposition_params)
        if is_attachment or kind not in {"text", "multipart"}:
            has_attachments = True
        if kind == "text" and subtype in {"plain", "html"} and not is_attachment:
            sections.append((path or "1", f"text/{subtype}", params.get("charset", "utf-8"), string(part[5], "7bit")))

    walk(structure, "")
    sections.sort(key=lambda item: item[1] != "text/plain")
    return sections, has_attachments


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
            imap.login(self.config.email, self.config.app_password.get_secret_value())
            yield imap

    @staticmethod
    def _fetch(imap: IMAPClient, uids: list[int], max_body_chars: int = 4000) -> list[dict]:
        if not uids:
            return []
        fetched = imap.fetch(uids, ["BODY.PEEK[HEADER]", "BODYSTRUCTURE"])
        result = []
        for uid in uids:
            if uid not in fetched:
                continue
            item = fetched[uid]
            header = item.get(b"BODY[HEADER]")
            structure = item.get(b"BODYSTRUCTURE")
            if not isinstance(header, bytes) or not structure:
                raise MailError(f"IMAP response for UID {uid} is missing header or MIME structure")
            summary = parse_message(header, uid, max_body_chars=max_body_chars)
            parts, attachments = _text_sections(structure)
            summary["has_attachments"] = attachments
            if parts:
                section, content_type, charset, encoding = parts[0]
                response = imap.fetch([uid], [f"BODY.PEEK[{section}]<0.16384>"]).get(uid, {})
                raw = response.get(f"BODY[{section}]<0>".encode())
                if isinstance(raw, bytes):
                    # Parse only the selected, bounded text section; never fetch
                    # attachment sections to build a preview.
                    from email.message import EmailMessage
                    part = EmailMessage()
                    part["Content-Type"] = f'{content_type}; charset="{charset}"'
                    part["Content-Transfer-Encoding"] = encoding
                    part.set_payload(raw.decode("ascii", errors="surrogateescape"))
                    payload = part.get_payload(decode=True) or b""
                    try:
                        content = payload.decode(charset, errors="replace")
                    except LookupError:
                        content = payload.decode("utf-8", errors="replace")
                    if content_type == "text/html":
                        parser = _TextFromHTML()
                        parser.feed(content)
                        content = " ".join(parser.fragments)
                    summary["preview"] = re.sub(r"\s+", " ", content).strip()[:min(max_body_chars, 10000)]
            result.append(summary)
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
        """Search the server, falling back to the latest 300 previews if unsupported."""
        if not query.strip():
            raise MailValueError("query cannot be empty")
        limit = max(1, min(limit, 100))
        needle = query.casefold()
        with self.connect() as imap:
            imap.select_folder(folder, readonly=True)
            try:
                uids = sorted(imap.search(["OR", "OR", "FROM", query, "SUBJECT", query, "TEXT", query], charset="UTF-8"))[::-1]
            except imaplib.IMAP4.error:
                uids = sorted(imap.search(["ALL"]))[-300:][::-1]
            else:
                return self._fetch(imap, uids[:limit], max_body_chars=2000)
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
            item = imap.fetch([uid], ["RFC822.SIZE"]).get(uid, {})
            if not item:
                raise MailLookupError(f"Message UID {uid} not found in {folder}")
            size = item.get(b"RFC822.SIZE")
            if not isinstance(size, int) or size > 20 * 1024 * 1024:
                raise MailValueError("Message size is unavailable or exceeds the 20 MiB download limit")
            raw = imap.fetch([uid], ["BODY.PEEK[]"]).get(uid, {}).get(b"BODY[]")
            if not isinstance(raw, bytes):
                raise MailLookupError(f"Message UID {uid} not found in {folder}")
            return parse_message(raw, uid, max_body_chars=10000)

    def move_to_promotions(self, uid: int, confirm: bool = False) -> dict:
        valid_uid(uid)
        if not self.config.allow_write:
            raise MailPermissionError("Write operations are disabled. Set YANDEX_ALLOW_WRITE=true")
        if confirm is not True:
            raise MailPermissionError("Explicit confirm=true is required to move a message")
        folder = self.config.promotions_folder
        forbidden_names = {"inbox", "trash", "spam", "deleted", "junk", "drafts", "sent", "outbox",
                           "спам", "удалённые", "удаленные", "черновики", "отправленные", "исходящие"}
        if not folder.strip() or folder.strip().casefold() in forbidden_names:
            raise MailValueError("Choose a safe dedicated promotions folder")
        with self.connect() as imap:
            folders = imap.list_folders()
            matching = [(flags, delimiter, name) for flags, delimiter, name in folders if name.casefold() == folder.casefold()]
            if len(matching) > 1:
                raise MailValueError("Promotions folder name is ambiguous")
            if matching:
                flags, _, folder = matching[0]
                special = {str(flag.decode() if isinstance(flag, bytes) else flag).casefold() for flag in flags}
                if special & {"\\trash", "\\junk", "\\drafts", "\\sent", "\\all", "\\flagged", "\\inbox", "\\noselect"}:
                    raise MailValueError("Choose a safe dedicated promotions folder")
            if not imap.has_capability("MOVE"):
                raise MailError("IMAP server does not support MOVE")
            if not matching:
                imap.create_folder(folder)
            imap.select_folder("INBOX", readonly=False)
            if uid not in imap.search(["UID", str(uid)]):
                raise MailLookupError(f"Message UID {uid} not found in INBOX")
            imap.move([uid], folder)
        return {"uid": uid, "moved_to": folder, "deleted_permanently": False}
