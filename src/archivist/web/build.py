"""Static site generator: archivist build."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from archivist.config import ARCHIVE_ROOT, PAPERS_DIR, DOCS_DIR
from archivist.services.paper_store import list_papers, get_paper_dir
from archivist.web import static_asset_version
from archivist.web.data import load_docs
from archivist.web.titles import format_paper_title
from archivist.web.privacy import public_paper
from archivist.web.routes.reading import _normalize_company, _render_markdown, _resolve_wiki_links

import click


TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def build_site(output_dir: Path) -> None:
    """Generate the full static site."""
    output_dir = Path(output_dir)
    private_dir = ARCHIVE_ROOT / "site-private"
    if private_dir.resolve().is_relative_to(output_dir.resolve()) or output_dir.resolve().is_relative_to(private_dir.resolve()):
        raise ValueError("Static output must be separate from the private data directory")

    # Clean and create output
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    # Setup Jinja2 environment
    # autoescape must be on: Jinja defaults it to False, but Flask's dev-server
    # env has it on, so the static build silently diverged. Paper metadata is
    # prose that legitimately contains `<` (for example, "x<y"),
    # which was emitted raw and opened a bogus tag that swallowed every card
    # after it. Pre-rendered HTML (`content`, `toc`) already passes `| safe`.
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.globals["url_for"] = _static_url_for
    env.filters["paper_title"] = format_paper_title
    env.globals["request"] = _FakeRequest()
    env.globals["mode"] = "production"
    env.globals["css_version"] = static_asset_version("style.css")
    env.globals["paper_search_version"] = static_asset_version("paper-search.js")

    for key, filename in {
        "article_content_css_version": "article-content.css",
        "article_content_js_version": "article-content.js",
        "mailbox_css_version": "mailbox.css",
        "mailbox_js_version": "mailbox.js",
        "admin_auth_css_version": "admin-auth.css",
        "admin_auth_js_version": "admin-auth.js",
        "reading_list_css_version": "reading-list.css",
        "reading_detail_css_version": "reading-detail.css",
        "reading_detail_js_version": "reading-detail.js",
        "graph_layout_js_version": "graph-layout.js",
        "graph_model_js_version": "graph-model.js",
        "graph_map_js_version": "graph-map.js",
        "graph_map_css_version": "graph-map.css",
        "graph_explorer_js_version": "graph-explorer.js",
        "graph_explorer_css_version": "graph-explorer.css",
        "recommendation_map_js_version": "recommendation-map.js",
        "recommendation_map_css_version": "recommendation-map.css",
    }.items():
        env.globals[key] = static_asset_version(filename)

    # Copy static assets
    static_out = output_dir / "static"
    shutil.copytree(STATIC_DIR, static_out)

    # Read-only config can be mirrored on static origins. All message POSTs
    # must reach the single owning service so capacity is global.
    from archivist.services.mailbox import get_mailbox_settings
    mailbox_settings = get_mailbox_settings()
    mailbox_dir = output_dir / "api" / "mailbox"
    mailbox_dir.mkdir(parents=True, exist_ok=True)
    (mailbox_dir / "config").write_text(json.dumps({
        "enabled": mailbox_settings.enabled,
        "max_length": mailbox_settings.max_length,
        "max_messages": mailbox_settings.max_messages,
    }, separators=(",", ":")), encoding="utf-8")

    # Load all papers — only those with reading.md (skip brief-only)
    papers = list_papers()
    private_papers = {}
    enriched = []
    for p in papers:
        has_reading = (PAPERS_DIR / str(p.year) / p.slug / "reading.md").exists()
        if not has_reading:
            continue  # Skip brief-only papers from website
        private_papers[p.slug] = {"rating": p.rating, "rating_reason": p.rating_reason}
        p = public_paper(p)
        company = _normalize_company(p.affiliations)
        enriched.append({"meta": p, "company": company, "has_reading": True})

    # Sort by published_date desc by default
    enriched.sort(key=lambda x: x["meta"].published_date or "", reverse=True)

    # Collect filter options — categories are fixed (always show all 4 types)
    categories = ["generative-rec", "discriminative-rec", "llm", "other"]
    companies = sorted({p["company"] for p in enriched if p["company"]})
    # Load flat tag whitelist from config
    from archivist.services.tag_registry import load_whitelist
    tag_list = sorted(load_whitelist())

    # Build paper lookups for wiki links — same indexing as the dev server, so
    # a [[…]] link can't resolve in one and dangle in the other.
    from archivist.web.routes.reading import _build_paper_lookups
    by_arxiv, by_slug = _build_paper_lookups(enriched)

    # Load digests data
    from archivist.web.data import prepare_digests_data, build_paper_to_digests_index, render_digest_html
    digests = prepare_digests_data()
    digest_count = len(digests["daily"]) + len(digests["weekly"]) + len(digests["monthly"])
    doc_count = len(load_docs(DOCS_DIR))
    paper_to_digests = build_paper_to_digests_index()

    # Model search index shared across Reading / Graph / Benchmark tabs
    from archivist.web.data import prepare_model_index
    model_index = prepare_model_index()
    search_dir = output_dir / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "model-index.json").write_text(
        json.dumps(model_index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    click.echo(f"Wrote model index: {len(model_index['models'])} models → search/model-index.json")

    # Render papers list page
    env.globals["request"].path = "/reading/"
    click.echo(f"Rendering paper list ({len(enriched)} papers)...")
    template = env.get_template("reading/index.html")
    reading_dir = output_dir / "reading"
    reading_dir.mkdir(parents=True, exist_ok=True)
    html = template.render(
        doc_type="papers",
        papers=enriched,
        categories=categories,
        companies=companies,
        tag_list=tag_list,
        sort_by="published_date",
        total=len(enriched),
        digest_count=digest_count,
        latest_daily=digests["daily"][0] if digests["daily"] else None,
    )
    (reading_dir / "index.html").write_text(html, encoding="utf-8")

    # Render digests list page (?type=digests, but as a static directory /reading/digests/)
    digests_html = template.render(
        doc_type="digests",
        total=len(enriched),
        digest_count=digest_count,
        daily=digests["daily"],
        weekly=digests["weekly"],
        monthly=digests["monthly"],
    )
    digests_dir = reading_dir / "digests"
    digests_dir.mkdir(parents=True, exist_ok=True)
    (digests_dir / "index.html").write_text(digests_html, encoding="utf-8")

    # Render each paper detail page
    from archivist.web.routes.reading import (
        _get_related_papers,
        _build_related_index,
        _clean_arxiv_id,
    )
    related_index = _build_related_index(enriched)

    detail_template = env.get_template("reading/detail.html")
    rendered = 0
    search_content = {}
    for item in enriched:
        p = item["meta"]
        paper_dir = get_paper_dir(p.slug)
        if not paper_dir:
            continue
        reading_file = paper_dir / "reading.md"
        if not reading_file.exists():
            continue

        md_text = reading_file.read_text(encoding="utf-8")
        html_content = _render_markdown(md_text)
        toc_html = getattr(_render_markdown, "_last_toc", "")
        html_content = _resolve_wiki_links(html_content, by_arxiv, by_slug)
        search_content[p.slug] = html_content

        related = _get_related_papers(p, enriched, index=related_index)

        # Get appears_in from digest index
        clean_aid = _clean_arxiv_id(p.arxiv_id or "")
        appears_in = paper_to_digests.get(clean_aid, [])

        detail_dir = reading_dir / str(p.year) / p.slug
        detail_dir.mkdir(parents=True, exist_ok=True)

        html = detail_template.render(
            paper=p,
            company=item["company"],
            content=html_content,
            toc=toc_html,
            related=related,
            appears_in=appears_in,
        )
        (detail_dir / "index.html").write_text(html, encoding="utf-8")

        # Copy paper figures so reading.md's relative `figures/fig_XX.png`
        # links resolve under /reading/<year>/<slug>/
        src_figs = paper_dir / "figures"
        if src_figs.is_dir():
            dst_figs = detail_dir / "figures"
            if dst_figs.exists():
                shutil.rmtree(dst_figs)
            shutil.copytree(src_figs, dst_figs)
        rendered += 1

    # Index the same published reading corpus, excluding private metadata and
    # other site sections. Reuse rendered reports to avoid parsing them twice.
    from archivist.web.search import build_paper_search
    search_count = build_paper_search(
        search_dir / "pagefind",
        (item["meta"] for item in enriched),
        rendered_content=search_content,
    )
    click.echo(f"Built full-text search: {search_count} papers → search/pagefind/")

    # Render digest detail pages
    click.echo("Rendering digest detail pages...")
    digest_template = env.get_template("reading/digest_detail.html")
    digest_pages = 0
    for period_type in ["daily", "weekly", "monthly"]:
        for d_card in digests[period_type]:
            meta_dict, html_content = render_digest_html(period_type, d_card["id"])
            if not meta_dict:
                continue
            d_dir = reading_dir / "digest" / period_type / d_card["id"]
            d_dir.mkdir(parents=True, exist_ok=True)
            html = digest_template.render(meta=meta_dict, content=html_content)
            (d_dir / "index.html").write_text(html, encoding="utf-8")
            digest_pages += 1

    # Index redirect
    (output_dir / "index.html").write_text(
        '<meta http-equiv="refresh" content="0;url=/reading/">\n',
        encoding="utf-8",
    )

    env.globals["request"].path = "/login/"
    login_dir = output_dir / "login"
    login_dir.mkdir(exist_ok=True)
    (login_dir / "index.html").write_text(env.get_template("login.html").render(), encoding="utf-8")

    # Graph page
    env.globals["request"].path = "/graph/"
    click.echo("Rendering graph page...")
    from archivist.web.data import prepare_graph_data
    graph_data = prepare_graph_data()
    graph_dir = output_dir / "graph"
    graph_dir.mkdir(exist_ok=True)
    (graph_dir / "data.json").write_text(
        json.dumps(graph_data, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    graph_template = env.get_template("graph.html")
    (graph_dir / "index.html").write_text(
        graph_template.render(), encoding="utf-8"
    )

    # Benchmark page
    env.globals["request"].path = "/benchmark/"
    click.echo("Rendering benchmark page...")
    from archivist.web.data import prepare_benchmark_data
    bm_data = prepare_benchmark_data()
    bm_dir = output_dir / "benchmark"
    bm_dir.mkdir(exist_ok=True)
    (bm_dir / "data.json").write_text(
        json.dumps(bm_data, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    bm_template = env.get_template("benchmark.html")
    (bm_dir / "index.html").write_text(
        bm_template.render(), encoding="utf-8"
    )

    # ── Docs section ──
    env.globals["request"].path = "/docs/"
    click.echo("Rendering docs pages...")
    all_docs = load_docs(DOCS_DIR)
    docs_out = output_dir / "docs"
    docs_out.mkdir(parents=True, exist_ok=True)

    docs_list_tpl = env.get_template("docs/index.html")
    (docs_out / "index.html").write_text(
        docs_list_tpl.render(docs=all_docs), encoding="utf-8"
    )

    docs_detail_tpl = env.get_template("docs/detail.html")
    for doc in all_docs:
        html_content = _render_markdown(doc["body"])
        toc_html = getattr(_render_markdown, "_last_toc", "")
        slug_dir = docs_out / doc["slug"]
        slug_dir.mkdir(parents=True, exist_ok=True)
        (slug_dir / "index.html").write_text(
            docs_detail_tpl.render(doc=doc, content=html_content, toc=toc_html),
            encoding="utf-8",
        )
        # Copy figures if present
        src_figures = DOCS_DIR / doc["slug"] / "figures"
        if src_figures.is_dir():
            dst_figures = slug_dir / "figures"
            if dst_figures.exists():
                shutil.rmtree(dst_figures)
            shutil.copytree(src_figures, dst_figures)

    # Archived documents may have owner-only source images. Nginx must be able
    # to read their published copies after authorizing the request; leave the
    # original archive and the separate administrator export private.
    docs_out.chmod(0o755)
    for published in docs_out.rglob("*"):
        published.chmod(0o755 if published.is_dir() else 0o644)

    # The API reads this export outside the static root. Publish it atomically
    # so simultaneous authenticated requests never observe a partial document.
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_dir.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=".admin-data-", dir=private_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            json.dump({"papers": private_papers, "doc_count": doc_count}, destination, ensure_ascii=False)
        os.replace(temporary, private_dir / "admin-data.json")
    finally:
        Path(temporary).unlink(missing_ok=True)

    click.echo(f"Built: {rendered} reading pages + {digest_pages} digest pages + {len(all_docs)} docs + graph + benchmark → {output_dir}/")


def _static_url_for(endpoint, **kwargs):
    """Fake url_for for static site generation."""
    if endpoint == "static":
        return f"/static/{kwargs.get('filename', '')}"
    return "#"


class _FakeRequest:
    """Fake request object for template rendering. Path is mutable per page."""
    path = "/reading/"
