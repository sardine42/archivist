"""Bounded, private website inbox with durable anonymous-client throttling."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
import sqlite3
import stat
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import unquote, urlsplit

REQUEST_LIMIT = 8192
RATE_WINDOW = 600
RATE_COUNT = 3
RATE_CLIENT_LIMIT = 1024


@dataclass(frozen=True)
class MailboxSettings:
    enabled: bool = True
    max_length: int = 500
    max_messages: int = 200
    database: Path = Path("archive/mailbox/inbox.sqlite3")
    public_url: str = ""
    trust_proxy: bool = False
    allowed_origins: tuple[str, ...] = ()


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).lower() in {"1", "true", "yes", "on"}:
        return True
    if str(value).lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Mailbox boolean setting is invalid")


def get_mailbox_settings() -> MailboxSettings:
    """Read configuration without opening or creating the inbox database."""
    from archivist.config import ARCHIVE_ROOT, PROJECT_ROOT, load_config

    config = load_config()
    values = config.get("mailbox", {}) or {}
    database = Path(os.environ.get("ARCHIVIST_MAILBOX_DB") or values.get("database")
                    or ARCHIVE_ROOT / "mailbox" / "inbox.sqlite3").expanduser()
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    max_length = int(os.environ.get("ARCHIVIST_MAILBOX_MAX_LENGTH")
                     or values.get("max_length", 500))
    max_messages = int(os.environ.get("ARCHIVIST_MAILBOX_MAX_MESSAGES")
                       or values.get("max_messages", 200))
    if not 1 <= max_length <= 10000 or not 1 <= max_messages <= 10000:
        raise ValueError("Mailbox limits must be between 1 and 10000")
    public_url = str(os.environ.get("ARCHIVIST_MAILBOX_PUBLIC_URL")
                     or values.get("public_url")
                     or (config.get("site", {}) or {}).get("base_url", ""))
    if public_url:
        origin_tuple(public_url)
    raw_origins = os.environ.get("ARCHIVIST_MAILBOX_ALLOWED_ORIGINS")
    allowed_origins = (tuple(value.strip() for value in raw_origins.split(",") if value.strip())
                       if raw_origins is not None else values.get("allowed_origins", ()))
    if not isinstance(allowed_origins, (list, tuple)) or not all(isinstance(value, str) for value in allowed_origins):
        raise ValueError("Mailbox allowed_origins must be a list of website origins")
    for origin in allowed_origins:
        origin_tuple(origin)
    return MailboxSettings(
        enabled=_boolean(os.environ.get("ARCHIVIST_MAILBOX_ENABLED",
                                        values.get("enabled", True))),
        max_length=max_length,
        max_messages=max_messages,
        database=database,
        public_url=public_url,
        trust_proxy=_boolean(os.environ.get("ARCHIVIST_MAILBOX_TRUST_PROXY",
                                            values.get("trust_proxy", False))),
        allowed_origins=tuple(allowed_origins),
    )


def origin_tuple(value: str) -> tuple[str, str, int]:
    """Compare origins without treating an explicit default port as different."""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Invalid website origin")
    return (parsed.scheme.lower(), parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80))


class MailboxError(Exception):
    def __init__(self, code: str, message: str, status: int = 400,
                 retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


def validate_submission(payload: object, max_length: int) -> tuple[str, str]:
    if not isinstance(payload, dict) or set(payload) - {"message", "page", "website", "request_id"}:
        raise MailboxError("invalid", "留言格式有误，请重新提交。")
    submission_request_id(payload)
    message = payload.get("message")
    page = payload.get("page", "/")
    website = payload.get("website", "")
    if not all(isinstance(value, str) for value in (message, page, website)):
        raise MailboxError("invalid", "留言格式有误，请重新提交。")
    if website:
        raise MailboxError("invalid", "留言未能提交，请稍后再试。")
    message = message.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not message:
        raise MailboxError("empty", "请先写下留言。")
    if len(message) > max_length:
        raise MailboxError("too_long", f"留言最多 {max_length} 字。")
    if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t"
           for char in message):
        raise MailboxError("invalid", "留言包含不支持的控制字符。")
    # Source pages never include URLs, queries, fragments or encoded controls.
    decoded_page = unquote(page)
    if (len(page) > 2048 or not page.startswith("/") or page.startswith("//")
            or any(char in page for char in "\\?#")
            or any(char in decoded_page for char in "\\?#")
            or decoded_page.startswith("//")
            or any(unicodedata.category(char) in {"Cc", "Cs"} for char in decoded_page)):
        raise MailboxError("invalid", "页面地址格式有误。")
    return message, page


def submission_request_id(payload: dict) -> str | None:
    """An optional UUID makes retries safe after a lost HTTP response."""
    if "request_id" not in payload:
        return None
    value = payload["request_id"]
    if not isinstance(value, str) or len(value) != 36:
        raise MailboxError("invalid", "留言请求标识有误，请重新提交。")
    try:
        normalized = str(uuid.UUID(value))
    except ValueError:
        raise MailboxError("invalid", "留言请求标识有误，请重新提交。") from None
    return normalized


class MailboxStore:
    """SQLite transactions enforce both capacity and rate limits across workers.

    The file is private, contains no raw IP addresses, and sits outside the
    published site directory. Expiring client counters have a fixed row bound.
    """

    def __init__(self, settings: MailboxSettings, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.clock = clock
        self.path = settings.database
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not stat.S_ISREG(self.path.lstat().st_mode):
                raise ValueError("Mailbox database must be a regular file")
        else:
            os.close(descriptor)
        self.path.chmod(0o600)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    page TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    read_at INTEGER,
                    request_id TEXT
                )""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
            if "request_id" not in columns:
                connection.execute("ALTER TABLE messages ADD COLUMN request_id TEXT")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS messages_request_id ON messages(request_id)")
            connection.execute("""CREATE TABLE IF NOT EXISTS clients (
                    digest TEXT PRIMARY KEY,
                    started_at INTEGER NOT NULL,
                    count INTEGER NOT NULL
                )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""")
            connection.execute("INSERT OR IGNORE INTO state VALUES ('client_secret', ?)",
                               (secrets.token_hex(32),))

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA secure_delete = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def submit(self, message: str, page: str, client: str, request_id: str | None = None) -> int:
        # Keep validation at the storage boundary as well as at the HTTP boundary.
        message, page = validate_submission({"message": message, "page": page},
                                            self.settings.max_length)
        if request_id is not None:
            request_id = submission_request_id({"request_id": request_id})
        now = int(self.clock())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                previous = connection.execute("SELECT id, message, page FROM messages WHERE request_id = ?",
                                              (request_id,)).fetchone()
                if previous is not None:
                    if previous["message"] != message or previous["page"] != page:
                        raise MailboxError("request_conflict", "这份留言已提交，请刷新后重新留言。", 409)
                    return previous["id"]
            connection.execute("DELETE FROM clients WHERE started_at <= ?", (now - RATE_WINDOW,))
            secret = connection.execute("SELECT value FROM state WHERE key = 'client_secret'").fetchone()[0]
            digest = hmac.new(bytes.fromhex(secret), client.encode("utf-8"), hashlib.sha256).hexdigest()
            counter = connection.execute("SELECT started_at, count FROM clients WHERE digest = ?",
                                         (digest,)).fetchone()
            if counter and counter["count"] >= RATE_COUNT:
                raise MailboxError("rate_limit", "留言太频繁，请稍后再试。", 429,
                                   max(1, math.ceil(counter["started_at"] + RATE_WINDOW - now)))
            count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if count >= self.settings.max_messages:
                raise MailboxError("full", "信箱暂时已满，请稍后再来。", 503)
            if counter:
                connection.execute("UPDATE clients SET count = count + 1 WHERE digest = ?", (digest,))
            else:
                # A high number of distinct senders cannot grow this table forever.
                if connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0] >= RATE_CLIENT_LIMIT:
                    connection.execute("DELETE FROM clients WHERE digest = (SELECT digest FROM clients ORDER BY started_at, digest LIMIT 1)")
                connection.execute("INSERT INTO clients VALUES (?, ?, 1)", (digest, now))
            cursor = connection.execute("INSERT INTO messages (message, page, created_at, request_id) VALUES (?, ?, ?, ?)",
                                        (message, page, now, request_id))
            return int(cursor.lastrowid)

    def status(self) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS total, COUNT(*) - COUNT(read_at) AS unread FROM messages").fetchone()
        return {"total": row["total"], "unread": row["unread"],
                "max_messages": self.settings.max_messages,
                "max_length": self.settings.max_length}

    def list_messages(self, limit: int = 200, unread: bool = False) -> list[dict]:
        with self._connection() as connection:
            where = "WHERE read_at IS NULL" if unread else ""
            rows = connection.execute(f"SELECT * FROM messages {where} ORDER BY id DESC LIMIT ?",
                                      (max(1, min(limit, self.settings.max_messages)),)).fetchall()
        return [dict(row) for row in rows]

    def read_message(self, message_id: int, mark_read: bool = True) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            if row is None:
                return None
            if mark_read and row["read_at"] is None:
                connection.execute("UPDATE messages SET read_at = ? WHERE id = ?",
                                   (int(self.clock()), message_id))
                row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row)

    def delete_messages(self, message_ids: tuple[int, ...]) -> int:
        with self._connection() as connection:
            before = connection.total_changes
            connection.executemany("DELETE FROM messages WHERE id = ?", ((item,) for item in message_ids))
            return connection.total_changes - before

    def clear_messages(self) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            connection.execute("DELETE FROM messages")
        return count
