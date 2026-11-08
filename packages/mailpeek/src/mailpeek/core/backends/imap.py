"""IMAP backend (stdlib ``imaplib`` + ``email``). Read-only.

Credentials are read from the environment at connect time
(``MAILPEEK_IMAP_USER`` / ``MAILPEEK_IMAP_PASSWORD``) and are never stored in
config, returned, or logged. The mailbox is opened read-only and bodies are
fetched with ``BODY.PEEK`` so messages are not marked as read.

Not exercised by the offline test suite (needs a live server).
"""

from __future__ import annotations

import os

from ..errors import BackendError
from . import MailBackend


class ImapBackend(MailBackend):
    def __init__(self, host: str, port: int = 993, ssl: bool = True) -> None:
        self.host = host
        self.port = int(port)
        self.ssl = bool(ssl)
        self._conn = None

    def _client(self):
        if self._conn is not None:
            return self._conn
        import imaplib
        user = os.environ.get("MAILPEEK_IMAP_USER")
        password = os.environ.get("MAILPEEK_IMAP_PASSWORD")
        if not user or not password:
            raise BackendError(
                "IMAP credentials not set (MAILPEEK_IMAP_USER / MAILPEEK_IMAP_PASSWORD)")
        cls = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        try:
            conn = cls(self.host, self.port)
            conn.login(user, password)
        except Exception as exc:  # pragma: no cover - needs a live server
            raise BackendError(f"IMAP connect/login failed: {type(exc).__name__}") from None
        self._conn = conn
        return conn

    def _select(self, folder: str):
        conn = self._client()
        typ, _ = conn.select(folder, readonly=True)   # read-only mailbox
        if typ != "OK":
            raise BackendError(f"cannot select folder: {folder!r}")
        return conn

    def list_folders(self) -> list[str]:  # pragma: no cover - needs a live server
        conn = self._client()
        typ, data = conn.list()
        if typ != "OK":
            raise BackendError("IMAP LIST failed")
        names = []
        for raw in data:
            line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            names.append(line.rsplit(" ", 1)[-1].strip('"'))
        return names

    def _headers(self, conn, uid: bytes) -> dict:  # pragma: no cover - live server
        import email
        typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            raise BackendError("IMAP fetch (header) failed")
        msg = email.message_from_bytes(msg_data[0][1])
        return {"id": uid.decode(), "subject": msg.get("Subject", ""), "from": msg.get("From", "")}

    def list_messages(self, folder: str) -> list[dict]:  # pragma: no cover - live server
        conn = self._select(folder)
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            raise BackendError("IMAP search failed")
        return [self._headers(conn, uid) for uid in data[0].split()]

    def read_message(self, folder: str, message_id: str) -> dict:  # pragma: no cover - live server
        import email
        conn = self._select(folder)
        typ, msg_data = conn.uid("fetch", message_id.encode(), "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            raise BackendError(f"message not found: {message_id}")
        msg = email.message_from_bytes(msg_data[0][1])
        body = ""
        attachments = []
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_maintype() == "multipart":
                continue
            if "attachment" in disp:
                attachments.append(part.get_filename() or "")
            elif part.get_content_type() == "text/plain" and not body:
                payload = part.get_payload(decode=True) or b""
                body = payload.decode(errors="replace")
        return {"id": message_id, "subject": msg.get("Subject", ""),
                "from": msg.get("From", ""), "body": body, "attachments": attachments}

    def search(self, folder: str, query: str) -> list[dict]:  # pragma: no cover - live server
        conn = self._select(folder)
        typ, data = conn.uid("search", None, "TEXT", f'"{query}"')
        if typ != "OK":
            raise BackendError("IMAP search failed")
        return [self._headers(conn, uid) for uid in data[0].split()]

    def download_attachment(self, folder: str, message_id: str, name: str) -> bytes:  # pragma: no cover
        import email
        conn = self._select(folder)
        typ, msg_data = conn.uid("fetch", message_id.encode(), "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            raise BackendError(f"message not found: {message_id}")
        msg = email.message_from_bytes(msg_data[0][1])
        for part in msg.walk():
            if part.get_filename() == name:
                return part.get_payload(decode=True) or b""
        raise BackendError(f"attachment not found: {message_id}/{name}")
