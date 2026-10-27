"""Single-administrator password verification and bounded server-side sessions."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from archivist.services.mailbox import origin_tuple

SESSION_LIFETIME = 7 * 24 * 60 * 60
SESSION_LIMIT = 32
FAILURE_WINDOW = 15 * 60
FAILURE_LIMIT = 5
CLIENT_LIMIT = 2048
PASSWORD_LIMIT = 1024
COOKIE_NAME = "__Host-archivist_admin"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.@][A-Za-z0-9_.@-]{0,127}\Z")


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = True
    htpasswd_file: Path | None = None
    htpasswd_binary: str = "htpasswd"
    username: str = ""
    database: Path = Path("archive/mailbox/admin-sessions.sqlite3")
    private_data_file: Path | None = None
    public_url: str = ""
    allowed_origins: tuple[str, ...] = ()


def get_auth_settings() -> AuthSettings:
    """Environment-only credentials; no password or reusable account is bundled."""
    from archivist.config import ARCHIVE_ROOT, PROJECT_ROOT
    from archivist.services.mailbox import get_mailbox_settings

    mailbox = get_mailbox_settings()

    def path_setting(name: str, default: Path | None = None) -> Path | None:
        raw = os.environ.get(name)
        value = Path(raw).expanduser() if raw else default
        if value is not None and not value.is_absolute():
            value = PROJECT_ROOT / value
        return value

    raw_origins = os.environ.get("ARCHIVIST_AUTH_ALLOWED_ORIGINS")
    allowed_origins = (tuple(value.strip() for value in raw_origins.split(",") if value.strip())
                       if raw_origins is not None else mailbox.allowed_origins)
    public_url = os.environ.get("ARCHIVIST_AUTH_PUBLIC_URL", mailbox.public_url)
    if public_url:
        origin_tuple(public_url)
    for value in allowed_origins:
        origin_tuple(value)
    enabled_value = os.environ.get("ARCHIVIST_AUTH_ENABLED", "true").lower()
    if enabled_value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise ValueError("Invalid administrator authentication setting")
    return AuthSettings(
        enabled=enabled_value in {"true", "1", "yes", "on"},
        htpasswd_file=path_setting("ARCHIVIST_AUTH_HTPASSWD_FILE"),
        htpasswd_binary=os.environ.get("ARCHIVIST_AUTH_HTPASSWD_BINARY", "htpasswd"),
        username=os.environ.get("ARCHIVIST_AUTH_USERNAME", ""),
        database=path_setting("ARCHIVIST_AUTH_DB", ARCHIVE_ROOT / "mailbox" / "admin-sessions.sqlite3"),
        private_data_file=path_setting("ARCHIVIST_AUTH_PRIVATE_DATA_FILE", ARCHIVE_ROOT / "site-private" / "admin-data.json"),
        public_url=public_url,
        allowed_origins=allowed_origins,
    )


def configured_identity(settings: AuthSettings) -> tuple[str, str] | None:
    """Fail closed unless exactly one configured credential can be selected.

    A fingerprint invalidates sessions when the underlying password changes.
    The hash itself is never returned by an HTTP endpoint or sent to htpasswd.
    """
    if not settings.enabled or settings.htpasswd_file is None:
        return None
    try:
        if not stat.S_ISREG(settings.htpasswd_file.lstat().st_mode):
            return None
        with settings.htpasswd_file.open("r", encoding="utf-8") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            return None
        records = [line for line in raw.splitlines() if line and not line.startswith("#")]
        pairs = [line.split(":", 1) for line in records]
        if any(len(pair) != 2 or not USERNAME_PATTERN.fullmatch(pair[0]) or not pair[1] for pair in pairs):
            return None
        if settings.username:
            if not USERNAME_PATTERN.fullmatch(settings.username):
                return None
            matches = [(user, encoded) for user, encoded in pairs if user == settings.username]
            if len(matches) != 1:
                return None
        else:
            if len(pairs) != 1:
                return None
            matches = pairs
        user, encoded = matches[0]
        return user, hashlib.sha256(f"{user}:{encoded}".encode("utf-8")).hexdigest()
    except (OSError, UnicodeError):
        return None


def validate_credentials(username: object, password: object) -> bool:
    return (isinstance(username, str) and bool(USERNAME_PATTERN.fullmatch(username))
            and isinstance(password, str) and 1 <= len(password) <= PASSWORD_LIMIT
            and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in password))


def verify_password(settings: AuthSettings, username: str, password: str) -> bool:
    """Use Apache's existing verifier; passwords travel only over stdin."""
    identity = configured_identity(settings)
    if not identity or not validate_credentials(username, password):
        return False
    if not hmac.compare_digest(username.encode("utf-8"), identity[0].encode("utf-8")):
        return False
    binary = shutil.which(settings.htpasswd_binary)
    if not binary:
        raise OSError("Administrator password verifier is unavailable")
    try:
        result = subprocess.run(
            [binary, "-vi", str(settings.htpasswd_file), identity[0]],
            input=password + "\n", text=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
    except subprocess.TimeoutExpired:
        raise OSError("Administrator password verifier is unavailable") from None
    return result.returncode == 0


class AuthRateLimit(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Administrator sign-in is temporarily rate limited")
        self.retry_after = retry_after


class AuthStore:
    """Private SQLite state with hashed cookies and short-lived IP digests."""

    def __init__(self, settings: AuthSettings, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.path = settings.database
        self.clock = clock

    @contextmanager
    def _connection(self, *, create: bool = True) -> Iterator[sqlite3.Connection | None]:
        if not create and not self.path.exists():
            yield None
            return
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Fail closed for a shared directory rather than changing permissions
            # of unrelated files selected by an incorrect configuration.
            if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
                raise OSError("Administrator state directory must be private")
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not stat.S_ISREG(self.path.lstat().st_mode):
                    raise OSError("Administrator session database is unavailable")
            else:
                os.close(descriptor)
            self.path.chmod(0o600)
        elif not stat.S_ISREG(self.path.lstat().st_mode):
            raise OSError("Administrator session database is unavailable")
        connection = (sqlite3.connect(self.path, timeout=10) if create else
                      sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA secure_delete = ON")
        try:
            with connection:
                if create:
                    connection.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, username TEXT NOT NULL, csrf_token TEXT NOT NULL, credential TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)")
                    connection.execute("CREATE TABLE IF NOT EXISTS failures (digest TEXT PRIMARY KEY, started_at INTEGER NOT NULL, count INTEGER NOT NULL)")
                    connection.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    connection.execute("INSERT OR IGNORE INTO state VALUES ('client_secret', ?)", (secrets.token_hex(32),))
                    connection.commit()
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _token_digest(token: str | None) -> str | None:
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            return None
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def reserve_attempt(self, client: str) -> None:
        """Reserve before verifying so concurrent requests obey the same limit."""
        now = int(self.clock())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM failures WHERE started_at <= ?", (now - FAILURE_WINDOW,))
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            digest = self._client_digest(connection, client)
            previous = connection.execute("SELECT started_at, count FROM failures WHERE digest = ?", (digest,)).fetchone()
            if previous and previous["count"] >= FAILURE_LIMIT:
                raise AuthRateLimit(max(1, math.ceil(previous["started_at"] + FAILURE_WINDOW - now)))
            if previous:
                connection.execute("UPDATE failures SET count = count + 1 WHERE digest = ?", (digest,))
            else:
                if connection.execute("SELECT COUNT(*) FROM failures").fetchone()[0] >= CLIENT_LIMIT:
                    connection.execute("DELETE FROM failures WHERE digest = (SELECT digest FROM failures ORDER BY started_at, digest LIMIT 1)")
                connection.execute("INSERT INTO failures VALUES (?, ?, 1)", (digest, now))

    def _client_digest(self, connection: sqlite3.Connection, client: str) -> str:
        secret = connection.execute("SELECT value FROM state WHERE key = 'client_secret'").fetchone()[0]
        return hmac.new(bytes.fromhex(secret), client.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_session(self, username: str, credential: str, client: str,
                      previous_token: str | None = None) -> tuple[str, dict]:
        now = int(self.clock())
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            previous = self._token_digest(previous_token)
            if previous:
                connection.execute("DELETE FROM sessions WHERE digest = ?", (previous,))
            while connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= SESSION_LIMIT:
                connection.execute("DELETE FROM sessions WHERE digest = (SELECT digest FROM sessions ORDER BY created_at, digest LIMIT 1)")
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                               (self._token_digest(token), username, csrf_token, credential, now, now + SESSION_LIFETIME))
            connection.execute("DELETE FROM failures WHERE digest = ?", (self._client_digest(connection, client),))
        return token, {"username": username, "csrf_token": csrf_token}

    def session(self, token: str | None, identity: tuple[str, str] | None) -> dict | None:
        digest = self._token_digest(token)
        if not digest or not identity:
            return None
        with self._connection(create=False) as connection:
            if connection is None:
                return None
            try:
                row = connection.execute("SELECT username, csrf_token, credential, expires_at FROM sessions WHERE digest = ?", (digest,)).fetchone()
            except sqlite3.OperationalError:
                return None
        if (row is None or row["expires_at"] <= int(self.clock()) or row["username"] != identity[0]
                or not hmac.compare_digest(row["credential"], identity[1])):
            return None
        return {"username": row["username"], "csrf_token": row["csrf_token"]}

    def revoke(self, token: str | None) -> None:
        digest = self._token_digest(token)
        if not digest:
            return
        with self._connection(create=False) as connection:
            if connection is not None:
                connection.execute("DELETE FROM sessions WHERE digest = ?", (digest,))
