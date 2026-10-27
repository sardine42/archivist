"""Offline public-site privacy checks with fictional, temporary archive data."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from archivist.models import ModelGraph, PaperMeta
from archivist.services.admin_auth import AuthSettings
from archivist.services.mailbox import MailboxSettings
from archivist.web import create_app
from archivist.web.privacy import public_paper


class WebPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="archivist-web-privacy-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.papers = self.directory / "papers"
        self.docs = self.directory / "docs"
        self.paper_dir = self.papers / "2025" / "fictional-paper"
        self.figures = self.paper_dir / "figures"
        self.figures.mkdir(parents=True)
        self.paper = PaperMeta(
            id="2501.00001", title="Fictional public paper", slug="fictional-paper", year=2025,
            model_name="ExampleNet", arxiv_id="2501.00001", category=["other"],
            one_line_summary="A fictional public summary", rating=9,
            rating_reason="PRIVATE_USER_REASON_SENTINEL", notes="PRIVATE_USER_NOTES_SENTINEL",
            feedback_consumed=True, score=6, reading_score=7,
        )
        (self.paper_dir / "meta.json").write_text(json.dumps(asdict(self.paper)), encoding="utf-8")
        (self.paper_dir / "reading.md").write_text("# Public report\n\nFictional public paper body.", encoding="utf-8")
        (self.figures / "example.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.doc_dir = self.docs / "private-doc-slug"
        (self.doc_dir / "figures").mkdir(parents=True)
        (self.doc_dir / "reading.md").write_text(
            "---\ntitle: PRIVATE_DOC_TITLE_SENTINEL\n---\nPRIVATE_DOC_BODY_SENTINEL", encoding="utf-8")
        (self.doc_dir / "figures" / "private.svg").write_text("PRIVATE_DOC_IMAGE_SENTINEL", encoding="utf-8")
        (self.doc_dir / "figures" / "private.svg").chmod(0o600)
        self.auth_settings = AuthSettings(
            htpasswd_file=None, database=self.directory / "auth.sqlite3",
            private_data_file=self.directory / "admin-data.json", public_url="https://example.com",
        )
        patches = (
            patch("archivist.web.routes.reading.PAPERS_DIR", self.papers),
            patch("archivist.web.routes.reading.DOCS_DIR", self.docs),
            patch("archivist.web.routes.reading.list_papers", return_value=[self.paper]),
            patch("archivist.web.routes.reading.get_paper", side_effect=lambda slug: self.paper if slug == self.paper.slug else None),
            patch("archivist.web.routes.reading.get_paper_dir", side_effect=lambda slug: self.paper_dir if slug == self.paper.slug else None),
            patch("archivist.web.routes.reading.load_graph", return_value=ModelGraph()),
            patch("archivist.web.routes.reading._normalize_company", return_value=""),
            patch("archivist.web.routes.site.DOCS_DIR", self.docs),
            patch("archivist.web.data.PAPERS_DIR", self.papers),
            patch("archivist.web.data.DOCS_DIR", self.docs),
            patch("archivist.web.data.BENCHMARKS_DIR", self.directory / "benchmarks"),
            patch("archivist.web.data.prepare_digests_data", return_value={"daily": [], "weekly": [], "monthly": []}),
            patch("archivist.web.data.build_paper_to_digests_index", return_value={}),
            patch("archivist.web.data.load_graph", return_value=ModelGraph()),
            patch("archivist.web.data.list_datasets", return_value=[]),
            patch("archivist.web.data.get_datasets", return_value=[]),
            patch("archivist.services.tag_registry.load_whitelist", return_value={"example"}),
            patch("archivist.web.auth.get_auth_settings", return_value=self.auth_settings),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = create_app()
        self.app.config.update(TESTING=True, AUTH_SETTINGS=self.auth_settings)
        self.client = self.app.test_client()

    def assert_no_private_content(self, response):
        body = response.get_data(as_text=True)
        for marker in ("PRIVATE_USER_REASON_SENTINEL", "PRIVATE_USER_NOTES_SENTINEL",
                       "PRIVATE_DOC_TITLE_SENTINEL", "PRIVATE_DOC_BODY_SENTINEL", "PRIVATE_DOC_IMAGE_SENTINEL"):
            self.assertNotIn(marker, body)
        return body

    def test_public_paper_removes_personal_fields_without_mutating_source(self):
        original = asdict(self.paper)
        public = public_paper(self.paper)
        self.assertIsNot(public, self.paper)
        self.assertIsNone(public.rating)
        self.assertEqual(public.rating_reason, "")
        self.assertEqual(public.notes, "")
        self.assertFalse(public.feedback_consumed)
        self.assertEqual(public.title, self.paper.title)
        self.assertEqual(public.score, 6)
        self.assertEqual(public.reading_score, 7)
        self.assertEqual(asdict(self.paper), original)

    def test_anonymous_list_and_detail_omit_real_ratings_and_personal_documents(self):
        for url in ("/reading/", "/reading/?sort=rating", "/reading/?type=digests",
                    "/reading/2025/fictional-paper/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                body = self.assert_no_private_content(response)
                self.assertNotIn('data-rating="9"', body)
                self.assertNotRegex(body, r"用户\s*<b>\s*9\s*</b>")
        self.assertEqual(self.paper.rating, 9)
        self.assertEqual(self.paper.rating_reason, "PRIVATE_USER_REASON_SENTINEL")

    def test_anonymous_documents_and_figures_are_protected_before_rendering(self):
        for url in ("/docs/", "/docs/private-doc-slug/", "/docs/private-doc-slug/figures/private.svg"):
            for method in ("GET", "HEAD"):
                with self.subTest(url=url, method=method):
                    response = self.client.open(url, method=method)
                    self.assertIn(response.status_code, (302, 303, 401))
                    self.assert_no_private_content(response)
                    self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertFalse(self.auth_settings.database.exists())

    def test_paper_figures_cannot_read_metadata_or_escape_via_symlink(self):
        (self.figures / "private.svg").symlink_to(self.paper_dir / "meta.json")
        paths = (
            "/reading/2025/fictional-paper/figures/../meta.json",
            "/reading/2025/fictional-paper/figures/%2e%2e/meta.json",
            "/reading/2025/fictional-paper/figures/../reading.md",
            "/reading/2025/fictional-paper/figures/private.svg",
            "/reading/2025/fictional-paper/figures/.",
            "/reading/2024/fictional-paper/figures/example.svg",
        )
        for url in paths:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assert_no_private_content(response)
                self.assertNotIn("Fictional public paper body.", response.get_data(as_text=True))
        with self.client.get("/reading/2025/fictional-paper/figures/example.svg") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<svg", response.data)

    def test_authenticated_document_figures_still_cannot_escape_directory(self):
        (self.doc_dir / "figures" / "escape.svg").symlink_to(self.doc_dir / "reading.md")
        with patch("archivist.web.auth.current_admin", return_value={"username": "fictional-admin"}):
            for url in ("/docs/private-doc-slug/figures/../reading.md",
                        "/docs/private-doc-slug/figures/%2e%2e/reading.md",
                        "/docs/private-doc-slug/figures/escape.svg"):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 404)
                    self.assert_no_private_content(response)

    def test_public_json_and_search_source_ignore_personal_metadata(self):
        for url in ("/graph/data.json", "/benchmark/data.json", "/search/model-index.json"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                body = self.assert_no_private_content(response)
                self.assertNotIn('"rating_reason"', body)
                self.assertNotIn('"feedback_consumed"', body)
        from archivist.web.search import _search_document
        source = _search_document(self.paper, "<p>Fictional public report</p>")
        self.assertIn("Fictional public report", source)
        self.assertNotIn("PRIVATE_USER_REASON_SENTINEL", source)
        self.assertNotIn("PRIVATE_USER_NOTES_SENTINEL", source)

    def test_static_build_keeps_personal_metadata_outside_public_output(self):
        from archivist.web.build import build_site
        from archivist.web.search import _search_document

        output = self.directory / "site"
        archive = self.directory / "archive"

        def index_source(destination, papers, rendered_content):
            destination.mkdir(parents=True)
            sources = [_search_document(paper, rendered_content[paper.slug]) for paper in papers]
            (destination / "test-corpus.html").write_text("\n".join(sources), encoding="utf-8")
            return len(sources)

        with patch("archivist.web.build.PAPERS_DIR", self.papers), \
                patch("archivist.web.build.DOCS_DIR", self.docs), \
                patch("archivist.web.build.ARCHIVE_ROOT", archive), \
                patch("archivist.web.build.list_papers", return_value=[self.paper]), \
                patch("archivist.web.build.get_paper_dir", return_value=self.paper_dir), \
                patch("archivist.services.mailbox.get_mailbox_settings", return_value=MailboxSettings()), \
                patch("archivist.web.search.build_paper_search", side_effect=index_source):
            build_site(output)

        for path in output.rglob("*"):
            if not path.is_file() or path.is_relative_to(output / "docs"):
                continue
            if path.suffix not in {".html", ".json"}:
                continue
            with self.subTest(path=str(path.relative_to(output))):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("PRIVATE_USER_REASON_SENTINEL", text)
                self.assertNotIn("PRIVATE_USER_NOTES_SENTINEL", text)
                self.assertNotIn("PRIVATE_DOC_TITLE_SENTINEL", text)
                self.assertNotIn("PRIVATE_DOC_BODY_SENTINEL", text)
                self.assertNotIn('data-rating="9"', text)

        private = archive / "site-private" / "admin-data.json"
        self.assertFalse(private.is_relative_to(output))
        self.assertEqual(json.loads(private.read_text()), {
            "papers": {"fictional-paper": {"rating": 9, "rating_reason": "PRIVATE_USER_REASON_SENTINEL"}},
            "doc_count": 1,
        })
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertIn("PRIVATE_DOC_TITLE_SENTINEL", (output / "docs" / "index.html").read_text())
        self.assertTrue((output / "docs" / "private-doc-slug" / "figures" / "private.svg").is_file())
        self.assertEqual((output / "docs" / "private-doc-slug" / "figures" / "private.svg").stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.doc_dir / "figures" / "private.svg").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
