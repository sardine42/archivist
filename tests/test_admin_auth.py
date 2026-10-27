"""Offline administrator-auth checks using only fictional temporary credentials."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archivist.services.admin_auth import (
    COOKIE_NAME,
    FAILURE_LIMIT,
    FAILURE_WINDOW,
    SESSION_LIFETIME,
    SESSION_LIMIT,
    AuthSettings,
    AuthStore,
    configured_identity,
    get_auth_settings,
    verify_password,
)
from archivist.services.mailbox import MailboxSettings
from archivist.web.auth import create_site_api_app


@unittest.skipUnless(shutil.which("htpasswd"), "Apache htpasswd is required for verification checks")
class AdminAuthTests(unittest.TestCase):
    username = "fictional_admin"
    password = "test-only-passphrase"
    origin = "https://example.com"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.password_file = self.directory / "credentials"
        self.password_file.write_text(self.credential_record(self.password), encoding="utf-8")
        self.password_file.chmod(0o600)
        self.private_file = self.directory / "private-data.json"
        self.private_file.write_text(json.dumps({"papers": {"fictional-paper": {
            "rating": 7, "rating_reason": "Fictional private note", "ignored": "not published"}},
            "doc_count": 2, "ignored": "not published"}), encoding="utf-8")
        self.settings = AuthSettings(htpasswd_file=self.password_file,
                                     database=self.directory / "sessions" / "sessions.sqlite3",
                                     private_data_file=self.private_file,
                                     public_url=self.origin)
        self.clock = [1000000]
        self.app = create_site_api_app(self.settings, MailboxSettings(database=self.directory / "mailbox.sqlite3"))
        self.app.config["AUTH_CLOCK"] = lambda: self.clock[0]
        self.client = self.app.test_client()

    def credential_record(self, password):
        return subprocess.run([shutil.which("htpasswd"), "-nmi", self.username],
                              input=password + "\n", text=True, capture_output=True,
                              timeout=5, check=True).stdout.strip() + "\n"

    def post(self, path, payload=None, headers=None, client=None):
        request_headers = {"Origin": self.origin}
        if headers:
            request_headers.update(headers)
        return (client or self.client).post(path, json={} if payload is None else payload,
                                            headers=request_headers, base_url=self.origin)

    def login(self, **kwargs):
        return self.post("/api/auth/login", {"username": self.username, "password": self.password}, **kwargs)

    def test_real_apr1_verifier_uses_stdin_without_password_in_argv(self):
        self.assertIn("$apr1$", self.password_file.read_text())
        self.assertTrue(verify_password(self.settings, self.username, self.password))
        self.assertFalse(verify_password(self.settings, self.username, "incorrect"))
        with patch("archivist.services.admin_auth.subprocess.run", wraps=subprocess.run) as execute:
            self.assertTrue(verify_password(self.settings, self.username, self.password))
            arguments, options = execute.call_args
            self.assertNotIn(self.password, repr(arguments))
            self.assertEqual(options["input"], self.password + "\n")
            self.assertNotIn("shell", options)
            self.assertEqual(options["stdout"], subprocess.DEVNULL)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)

    def test_fail_closed_without_credentials_and_no_public_get(self):
        app = create_site_api_app(replace(self.settings, htpasswd_file=None), MailboxSettings())
        client = app.test_client()
        response = self.post("/api/auth/session", client=client)
        self.assertEqual(response.json, {"enabled": False, "authenticated": False})
        self.assertEqual(self.login(client=client).status_code, 503)
        self.assertEqual(self.post("/api/admin/data", client=client).status_code, 401)
        self.assertFalse(self.settings.database.exists())
        for path in ("/api/auth/session", "/api/auth/login", "/api/auth/logout", "/api/auth/check", "/api/admin/data"):
            response = self.client.get(path, base_url=self.origin)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            self.assertNotIn("Fictional private note", response.get_data(as_text=True))
            self.assertNotIn("WWW-Authenticate", response.headers)

    def test_session_and_private_data_are_authorized_and_no_store(self):
        self.assertEqual(self.post("/api/auth/session").json, {"enabled": True, "authenticated": False})
        self.assertFalse(self.settings.database.exists())
        self.assertEqual(self.post("/api/admin/data").status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["authenticated"])
        self.assertEqual(response.json["username"], self.username)
        header = response.headers["Set-Cookie"]
        for required in (COOKIE_NAME, "Secure", "HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={SESSION_LIFETIME}"):
            self.assertIn(required, header)
        self.assertNotIn("Domain=", header)
        self.assertNotIn("Set-Cookie", self.post("/api/auth/session").headers)
        data = self.post("/api/admin/data")
        self.assertEqual(data.json, {"papers": {"fictional-paper": {"rating": 7,
                                         "rating_reason": "Fictional private note"}}, "doc_count": 2})
        self.assertEqual(data.headers["Cache-Control"], "private, no-store")
        self.assertIn("Cookie", data.headers["Vary"])
        self.assertEqual(self.client.post("/api/auth/check", base_url=self.origin).status_code, 204)

    def test_cookie_token_is_hashed_and_files_are_private(self):
        self.login()
        token = self.client.get_cookie(COOKIE_NAME, domain="example.com").value
        with closing(sqlite3.connect(self.settings.database)) as connection:
            digest = connection.execute("SELECT digest FROM sessions").fetchone()[0]
        self.assertEqual(digest, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token.encode(), self.settings.database.read_bytes())
        self.assertNotIn(self.password.encode(), self.settings.database.read_bytes())
        self.assertEqual(stat.S_IMODE(self.settings.database.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.settings.database.parent.stat().st_mode), 0o700)

    def test_random_cookie_does_not_create_state(self):
        for token in ("bad", "A" * 43, "x" * 2000):
            self.client.set_cookie(COOKIE_NAME, token, domain="example.com", secure=True)
            self.assertEqual(self.post("/api/auth/session").json["authenticated"], False)
            self.assertEqual(self.client.post("/api/auth/check", base_url=self.origin).status_code, 401)
        self.assertFalse(self.settings.database.exists())

    def test_login_rotates_cookie_and_rejects_previous_token(self):
        self.login()
        previous = self.client.get_cookie(COOKIE_NAME, domain="example.com").value
        self.login()
        self.assertNotEqual(previous, self.client.get_cookie(COOKIE_NAME, domain="example.com").value)
        self.client.set_cookie(COOKIE_NAME, previous, domain="example.com", secure=True)
        self.assertFalse(self.post("/api/auth/session").json["authenticated"])

    def test_expired_session_and_changed_credentials_fail_closed(self):
        self.login()
        self.clock[0] += SESSION_LIFETIME
        self.assertFalse(self.post("/api/auth/session").json["authenticated"])
        self.login()
        self.password_file.write_text(self.credential_record("different-test-passphrase"), encoding="utf-8")
        self.assertFalse(self.post("/api/auth/session").json["authenticated"])

    def test_logout_checks_csrf_and_revokes_replayed_cookie(self):
        response = self.login()
        token = self.client.get_cookie(COOKIE_NAME, domain="example.com").value
        for csrf in ("", "incorrect"):
            self.assertEqual(self.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code, 403)
            self.assertTrue(self.post("/api/auth/session").json["authenticated"])
        response = self.post("/api/auth/logout", headers={"X-CSRF-Token": response.json["csrf_token"]})
        self.assertEqual(response.json, {"enabled": True, "authenticated": False})
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.client.set_cookie(COOKIE_NAME, token, domain="example.com", secure=True)
        self.assertFalse(self.post("/api/auth/session").json["authenticated"])
        self.assertEqual(self.client.post("/api/auth/check", base_url=self.origin).status_code, 401)

    def test_origin_is_required_except_cookie_only_internal_check(self):
        self.login()
        for origin in ("", "null", "https://unrelated.example", "https://example.com/evil", "http://example.com"):
            for path in ("/api/auth/session", "/api/auth/login", "/api/auth/logout", "/api/admin/data"):
                with self.subTest(path=path, origin=origin):
                    self.assertEqual(self.post(path, headers={"Origin": origin}).status_code, 403)
        self.assertEqual(self.post("/api/auth/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/check", base_url=self.origin).status_code, 204)
        self.assertTrue(self.post("/api/auth/session", headers={"Origin": self.origin + ":443"}).json["authenticated"])

    def test_invalid_body_credentials_and_request_size(self):
        for payload in ([], {"username": self.username}, {"username": self.username, "password": self.password, "extra": True}):
            self.assertEqual(self.post("/api/auth/login", payload).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/session", data="{}", headers={"Origin": self.origin}, base_url=self.origin).status_code, 415)
        self.assertEqual(self.client.post("/api/auth/login", data="x" * 9000,
                                         headers={"Origin": self.origin, "Content-Type": "application/json"}, base_url=self.origin).status_code, 413)
        for password in ("", "bad\npassword", "bad\x00password", "x" * 1025):
            response = self.post("/api/auth/login", {"username": self.username, "password": password})
            self.assertEqual(response.status_code, 401)
            if password:
                self.assertNotIn(password, response.get_data(as_text=True))

    def test_failed_login_limit_expires_without_storing_ip(self):
        for _ in range(FAILURE_LIMIT):
            self.assertEqual(self.post("/api/auth/login", {"username": self.username, "password": "incorrect"}).status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(int(response.headers["Retry-After"]), FAILURE_WINDOW)
        self.assertNotIn(b"127.0.0.1", self.settings.database.read_bytes())
        self.clock[0] += FAILURE_WINDOW
        self.assertEqual(self.login().status_code, 200)
        with closing(sqlite3.connect(self.settings.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM failures").fetchone()[0], 0)

    def test_session_and_client_tables_are_bounded(self):
        store = AuthStore(self.settings, lambda: self.clock[0])
        identity = configured_identity(self.settings)
        with patch("archivist.services.admin_auth.CLIENT_LIMIT", 4):
            for index in range(10):
                store.reserve_attempt(f"fictional-client-{index}")
            with closing(sqlite3.connect(self.settings.database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM failures").fetchone()[0], 4)
        for _ in range(SESSION_LIMIT + 3):
            store.issue_session(identity[0], identity[1], "fictional-client")
        with closing(sqlite3.connect(self.settings.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], SESSION_LIMIT)

    def test_ambiguous_credentials_missing_verifier_and_private_data_shape(self):
        self.password_file.write_text(self.password_file.read_text() + "another_user:$apr1$fictional\n", encoding="utf-8")
        self.assertIsNone(configured_identity(self.settings))
        explicit = replace(self.settings, username=self.username)
        self.assertIsNotNone(configured_identity(explicit))
        with self.assertRaises(OSError):
            verify_password(replace(explicit, htpasswd_binary="nonexistent-test-verifier"), self.username, self.password)
        self.password_file.write_text(self.credential_record(self.password), encoding="utf-8")
        self.login()
        self.private_file.write_text('{"papers":{"fictional":{"rating":11}},"doc_count":0}', encoding="utf-8")
        self.assertEqual(self.post("/api/admin/data").status_code, 503)
        self.private_file.unlink()
        self.assertEqual(self.post("/api/admin/data").status_code, 503)

    def test_concurrent_logins_cannot_bypass_attempt_reservation(self):
        def attempt(_):
            client = self.app.test_client()
            return self.post("/api/auth/login", {"username": self.username, "password": "incorrect"}, client=client).status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            statuses = list(executor.map(attempt, range(12)))
        self.assertEqual(statuses.count(401), FAILURE_LIMIT)
        self.assertEqual(statuses.count(429), 12 - FAILURE_LIMIT)

    def test_password_verifier_timeout_and_shared_directory_fail_closed(self):
        timeout = subprocess.TimeoutExpired(["htpasswd"], 5)
        with patch("archivist.services.admin_auth.subprocess.run", side_effect=timeout):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(self.password, response.get_data(as_text=True))
        self.settings.database.parent.chmod(0o755)
        response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.post("/api/auth/session").json["authenticated"])

    def test_environment_settings_reuse_mailbox_origin_and_support_private_paths(self):
        variables = {"ARCHIVIST_AUTH_HTPASSWD_FILE": str(self.password_file),
                     "ARCHIVIST_AUTH_DB": str(self.settings.database),
                     "ARCHIVIST_AUTH_PRIVATE_DATA_FILE": str(self.private_file),
                     "ARCHIVIST_AUTH_USERNAME": self.username,
                     "ARCHIVIST_MAILBOX_PUBLIC_URL": self.origin,
                     "ARCHIVIST_AUTH_ALLOWED_ORIGINS": "https://example.com,https://www.example.com"}
        with patch.dict(os.environ, variables, clear=True):
            settings = get_auth_settings()
        self.assertEqual(settings.htpasswd_file, self.password_file)
        self.assertEqual(settings.database, self.settings.database)
        self.assertEqual(settings.public_url, self.origin)
        self.assertEqual(settings.allowed_origins, (self.origin, "https://www.example.com"))


if __name__ == "__main__":
    unittest.main()
