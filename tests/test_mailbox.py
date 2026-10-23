"""Offline checks for mailbox boundaries; all state lives in temporary folders."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from archivist.mailbox_cli import mailbox
from archivist.services.mailbox import (
    RATE_CLIENT_LIMIT,
    MailboxError,
    MailboxSettings,
    MailboxStore,
    get_mailbox_settings,
    validate_submission,
)
from archivist.web.mailbox import create_mailbox_app


@contextmanager
def database_connection(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = MailboxSettings(
            database=Path(self.temporary.name) / "private" / "inbox.sqlite3",
            public_url="https://example.com",
        )
        self.client = create_mailbox_app(self.settings).test_client()

    def post(self, message="Example message", **kwargs):
        return self.client.post("/api/mailbox/messages", json={"message": message, "page": "/reading/", "website": ""},
                                headers={"Origin": "https://example.com"}, **kwargs)

    def test_config_does_not_create_database(self):
        response = self.client.get("/api/mailbox/config")
        self.assertEqual(response.json, {"enabled": True, "max_length": 500, "max_messages": 200})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertFalse(self.settings.database.exists())

    def test_unicode_boundaries_and_roundtrip(self):
        message = "汉😀" * 250
        self.assertEqual(self.post(message).status_code, 201)
        self.assertEqual(self.post(message + "字").json["error"]["code"], "too_long")
        stored = MailboxStore(self.settings).list_messages()
        self.assertEqual(stored[0]["message"], message)
        self.assertEqual(stored[0]["page"], "/reading/")
        self.assertEqual(stat.S_IMODE(self.settings.database.stat().st_mode), 0o600)

    def test_empty_invalid_and_control_fields(self):
        for payload in (
            {"message": " \n\t "}, {"message": 5}, {"message": "ok", "website": True},
            {"message": "ok", "website": "bot"}, {"message": "bad\x1b[2J"},
            {"message": "\ud800"}, {"message": "ok", "page": []},
            {"message": "ok", "extra": "field"}, ["message"],
        ):
            with self.subTest(payload=repr(payload)):
                response = self.client.post("/api/mailbox/messages", json=payload,
                                            headers={"Origin": "https://example.com"})
                self.assertEqual(response.status_code, 400)
        self.assertFalse(self.settings.database.exists())
        self.assertEqual(validate_submission({"message": " a\r\nb\t "}, 500)[0], "a\nb")

    def test_page_is_a_path_without_private_query_or_external_url(self):
        for page in ("https://example.com/", "//example.com/", "/a?token=secret", "/a#fragment",
                     "/a\\b", "/%0Aabc", "/%2Fexternal", "/a%3Ftoken=secret", "/a\x00"):
            with self.subTest(page=page), self.assertRaises(MailboxError):
                validate_submission({"message": "ok", "page": page}, 500)
        self.assertEqual(validate_submission({"message": "ok", "page": "/reading/paper/"}, 500)[1], "/reading/paper/")

    def test_body_limit_and_malformed_json(self):
        headers = {"Origin": "https://example.com", "Content-Type": "application/json"}
        self.assertEqual(self.client.post("/api/mailbox/messages", data="{" + "a" * 9000, headers=headers).status_code, 413)
        self.assertEqual(self.client.post("/api/mailbox/messages", data="{", headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/mailbox/messages", data="[" * 1100 + "]" * 1100, headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/mailbox/messages", data="text", headers={"Origin": "https://example.com"}).status_code, 415)

    def test_cross_site_and_missing_origin_are_rejected(self):
        for origin in ("https://unrelated.example", "http://example.com", "null", "https://example.com/evil", ""):
            response = self.client.post("/api/mailbox/messages", json={"message": "ok"}, headers={"Origin": origin})
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/mailbox/messages", json={"message": "ok"},
                                    headers={"Origin": "https://example.com", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.settings.database.exists())
        self.assertEqual(self.client.post("/api/mailbox/messages", json={"message": "ok"},
                                         headers={"Origin": "https://example.com:443"}).status_code, 201)

    def test_no_public_read_delete_or_admin_routes(self):
        self.post("Private content")
        for method, path in (("GET", "/api/mailbox/messages"), ("GET", "/api/mailbox/messages/1"),
                             ("DELETE", "/api/mailbox/messages/1"), ("POST", "/api/mailbox/clear"),
                             ("GET", "/api/mailbox/status"), ("GET", "/")):
            response = self.client.open(path, method=method)
            self.assertIn(response.status_code, {404, 405})
            self.assertNotIn("Private content", response.get_data(as_text=True))

    def test_full_inbox_preserves_messages_and_recovers_after_delete(self):
        settings = replace(self.settings, max_messages=2)
        store = MailboxStore(settings)
        first = store.submit("First", "/", "client-a")
        store.submit("Second", "/", "client-b")
        with self.assertRaises(MailboxError) as caught:
            store.submit("Third", "/", "client-c")
        self.assertEqual((caught.exception.code, caught.exception.status), ("full", 503))
        self.assertEqual([item["message"] for item in store.list_messages()], ["Second", "First"])
        self.assertEqual(store.delete_messages((first,)), 1)
        store.submit("Third", "/", "client-c")
        self.assertEqual(store.status()["total"], 2)
        self.assertEqual(store.clear_messages(), 2)
        self.assertEqual(store.status()["total"], 0)

    def test_idempotent_retry_succeeds_after_full_without_consuming_rate_limit(self):
        settings = replace(self.settings, max_messages=1)
        client = create_mailbox_app(settings).test_client()
        request_id = str(uuid.uuid4())
        payload = {"message": "One message", "page": "/reading/", "request_id": request_id}
        for _ in range(5):
            response = client.post("/api/mailbox/messages", json=payload,
                                   headers={"Origin": "https://example.com"})
            self.assertEqual(response.status_code, 201)
        store = MailboxStore(settings)
        self.assertEqual(store.status()["total"], 1)
        with database_connection(settings.database) as connection:
            self.assertEqual(connection.execute("SELECT count FROM clients").fetchone()[0], 1)
        conflicting = dict(payload, message="Different message")
        self.assertEqual(client.post("/api/mailbox/messages", json=conflicting,
                                     headers={"Origin": "https://example.com"}).status_code, 409)
        conflicting = dict(payload, page="/graph/")
        self.assertEqual(client.post("/api/mailbox/messages", json=conflicting,
                                     headers={"Origin": "https://example.com"}).status_code, 409)
        self.assertEqual(store.list_messages()[0]["message"], "One message")
        self.assertEqual(client.post("/api/mailbox/messages", json={"message": "New message", "request_id": str(uuid.uuid4())},
                                     headers={"Origin": "https://example.com"}).json["error"]["code"], "full")

    def test_concurrent_idempotent_submissions_create_one_message(self):
        settings = replace(self.settings, max_messages=1)
        request_id = str(uuid.uuid4())
        MailboxStore(settings)

        def send(_):
            return MailboxStore(settings).submit("Single message", "/", "client", request_id)

        with ThreadPoolExecutor(max_workers=10) as executor:
            ids = list(executor.map(send, range(20)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(MailboxStore(settings).status()["total"], 1)

    def test_request_id_validation_and_legacy_database_migration(self):
        for request_id in ("", "not-a-uuid", None, 7, "0" * 36):
            with self.assertRaises(MailboxError):
                validate_submission({"message": "Example", "request_id": request_id}, 500)
        self.settings.database.parent.mkdir()
        with database_connection(self.settings.database) as connection:
            connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, page TEXT NOT NULL, created_at INTEGER NOT NULL, read_at INTEGER)")
            connection.execute("INSERT INTO messages (message, page, created_at) VALUES ('Earlier message', '/', 1000)")
        store = MailboxStore(self.settings)
        request_id = str(uuid.uuid4())
        stored_id = store.submit("New message", "/", "client", request_id)
        self.assertEqual(store.submit("New message", "/", "client", request_id), stored_id)
        self.assertEqual(store.status()["total"], 2)

    def test_allowed_origin_aliases_are_explicit(self):
        settings = replace(self.settings, allowed_origins=("https://example.com", "https://www.example.com"))
        client = create_mailbox_app(settings).test_client()
        self.assertEqual(client.post("/api/mailbox/messages", json={"message": "Alias message"},
                                     headers={"Origin": "https://www.example.com"}).status_code, 201)
        self.assertEqual(self.client.post("/api/mailbox/messages", json={"message": "Alias message"},
                                          headers={"Origin": "https://www.example.com"}).status_code, 403)
        self.assertEqual(client.post("/api/mailbox/messages", json={"message": "Unlisted host"},
                                     headers={"Origin": "https://other.example.com", "Host": "other.example.com"}).status_code, 403)

    def test_concurrent_submissions_cannot_exceed_capacity(self):
        settings = replace(self.settings, max_messages=7)
        MailboxStore(settings)

        def send(index):
            try:
                MailboxStore(settings).submit(f"Message {index}", "/", f"client-{index}")
                return "ok"
            except MailboxError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=12) as executor:
            outcomes = list(executor.map(send, range(30)))
        self.assertEqual(outcomes.count("ok"), 7)
        self.assertEqual(outcomes.count("full"), 23)
        self.assertEqual(MailboxStore(settings).status()["total"], 7)

    def test_rate_limit_persists_and_expires_across_store_instances(self):
        now = [1000]
        for index in range(3):
            MailboxStore(self.settings, lambda: now[0]).submit(f"Message {index}", "/", "192.0.2.10")
        with self.assertRaises(MailboxError) as caught:
            MailboxStore(self.settings, lambda: now[0]).submit("Fourth", "/", "192.0.2.10")
        self.assertEqual((caught.exception.code, caught.exception.retry_after), ("rate_limit", 600))
        now[0] += 601
        MailboxStore(self.settings, lambda: now[0]).submit("After cooldown", "/", "192.0.2.10")
        with database_connection(self.settings.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0], 1)
        self.assertNotIn(b"192.0.2.10", self.settings.database.read_bytes())

    def test_rate_limit_has_fixed_storage_bound(self):
        store = MailboxStore(self.settings, lambda: 1000)
        with database_connection(self.settings.database) as connection:
            connection.executemany("INSERT INTO clients VALUES (?, 1000, 1)", ((f"old-{i}",) for i in range(RATE_CLIENT_LIMIT)))
        store.submit("New sender", "/", "new-client")
        with database_connection(self.settings.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0], RATE_CLIENT_LIMIT)

    def test_http_rate_limit_and_explicit_proxy_trust(self):
        for _ in range(3):
            self.assertEqual(self.post(environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 201)
        response = self.post(environ_overrides={"REMOTE_ADDR": "192.0.2.1"})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        # Forwarded headers cannot change client identity unless explicitly trusted.
        response = self.client.post("/api/mailbox/messages", json={"message": "Blocked"},
                                    headers={"Origin": "https://example.com", "X-Forwarded-For": "192.0.2.2"},
                                    environ_overrides={"REMOTE_ADDR": "192.0.2.1"})
        self.assertEqual(response.status_code, 429)
        trusted = create_mailbox_app(replace(self.settings, trust_proxy=True)).test_client()
        self.assertEqual(trusted.post("/api/mailbox/messages", json={"message": "Separate sender"},
                                     headers={"Origin": "https://example.com", "X-Forwarded-For": "192.0.2.2"}).status_code, 201)

    def test_cli_private_read_and_confirmed_cleanup(self):
        store = MailboxStore(self.settings)
        message_id = store.submit("Example private message", "/reading/", "client")
        runner = CliRunner()
        args = ["--database", str(self.settings.database)]
        with patch("archivist.mailbox_cli.get_mailbox_settings", return_value=self.settings):
            listing = runner.invoke(mailbox, args + ["list", "--json"])
            self.assertEqual(listing.exit_code, 0, listing.output)
            self.assertEqual(json.loads(listing.output)[0]["message"], "Example private message")
            self.assertEqual(store.status()["unread"], 1)
            result = runner.invoke(mailbox, args + ["read", str(message_id), "--json"])
            self.assertEqual(json.loads(result.output)["id"], message_id)
            self.assertEqual(store.status()["unread"], 0)
            self.assertNotEqual(runner.invoke(mailbox, args + ["clear"], input="n\n").exit_code, 0)
            self.assertEqual(store.status()["total"], 1)
            self.assertEqual(runner.invoke(mailbox, args + ["delete", str(message_id), "--yes"]).exit_code, 0)
            self.assertEqual(store.status()["total"], 0)

    def test_environment_overrides_configuration_without_creating_database(self):
        path = Path(self.temporary.name) / "override.sqlite3"
        with patch.dict(os.environ, {"ARCHIVIST_MAILBOX_DB": str(path), "ARCHIVIST_MAILBOX_MAX_LENGTH": "123",
                                     "ARCHIVIST_MAILBOX_MAX_MESSAGES": "45", "ARCHIVIST_MAILBOX_PUBLIC_URL": "https://example.com",
                                     "ARCHIVIST_MAILBOX_TRUST_PROXY": "1", "ARCHIVIST_MAILBOX_ALLOWED_ORIGINS": "https://example.com, https://www.example.com"}):
            settings = get_mailbox_settings()
        self.assertEqual((settings.database, settings.max_length, settings.max_messages, settings.trust_proxy),
                         (path, 123, 45, True))
        self.assertFalse(path.exists())
        self.assertEqual(settings.allowed_origins, ("https://example.com", "https://www.example.com"))


if __name__ == "__main__":
    unittest.main()
