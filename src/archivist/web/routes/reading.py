"""Paper-Reading routes."""

import re
from pathlib import Path

import markdown
from flask import Blueprint, abort, redirect, render_template, request, send_file

from archivist.config import DOCS_DIR, PAPERS_DIR
from archivist.services.paper_store import get_paper, get_paper_dir, list_papers
from archivist.services.dag import load_graph

reading_bp = Blueprint("reading", __name__, url_prefix="/reading")

# Reuse canonical company normalization
from archivist.web.data import normalize_company as _normalize_company


def _protect_latex(text: str) -> tuple[str, list[str]]:
    """Replace LaTeX blocks with placeholders before Markdown processing.

    Protects both display math ($$...$$) and inline math ($...$) from
    being mangled by the Markdown parser (which treats _ as emphasis).

    Also rewrites bare ``<`` / ``>`` to the HTML entities ``&lt;`` / ``&gt;``
    inside the LaTeX content. The browser otherwise tries to parse ``y_{<j}``
    as the start of an HTML tag, breaking the DOM before KaTeX can run on the
    textContent. The entities survive the HTML parse and decode back to literal
    ``<`` / ``>`` in textContent, which KaTeX renders as a relation in math mode
    (identical to ``\\lt``) AND as a literal character inside ``\\texttt{}`` /
    ``\\text{}`` text mode. Using ``\\lt`` / ``\\gt`` instead would break the
    text-mode case (those commands are undefined there — e.g. semantic-id token
    strings like ``\\texttt{<|domain_begin|><a_5028>}`` print a literal ``\\lt``).
    """
    placeholders = []

    def _escape_lt_gt(body: str) -> str:
        # Replace bare `<` / `>` (not preceded by backslash) with HTML entities
        # so they survive the HTML parse and render correctly in BOTH math and
        # text mode once KaTeX reads the decoded textContent.
        body = re.sub(r"(?<!\\)<", "&lt;", body)
        body = re.sub(r"(?<!\\)>", "&gt;", body)
        return body

    def store(match):
        placeholders.append(_escape_lt_gt(match.group(0)))
        return f"\x00LATEX{len(placeholders) - 1}\x00"

    # Display math first (greedy across lines)
    text = re.sub(r"\$\$[\s\S]+?\$\$", store, text)
    # Inline math (non-greedy, single line)
    text = re.sub(r"\$(?!\$)(?:[^\$\n]|\\\$)+\$", store, text)
    return text, placeholders


def _restore_latex(html: str, placeholders: list[str]) -> str:
    """Restore LaTeX blocks after Markdown processing."""
    for i, original in enumerate(placeholders):
        html = html.replace(f"\x00LATEX{i}\x00", original)
    return html


def _fix_list_spacing(text: str) -> str:
    """Ensure blank line before list blocks so Markdown parses them correctly.

    Many reading.md files have lists immediately after a paragraph line
    (no blank line), which standard Markdown doesn't treat as a list.
    """
    # Insert blank line before a line starting with "- " if the previous line
    # is non-empty and not itself a list item or blank
    lines = text.split("\n")
    result = []
    for i, line in enumerate(lines):
        if (line.startswith("- ") or line.startswith("* ")) and i > 0:
            prev = result[-1] if result else ""
            if prev.strip() and not prev.startswith("- ") and not prev.startswith("* "):
                result.append("")
        result.append(line)
    return "\n".join(result)


def _render_markdown(text: str) -> str:
    """Render Markdown to HTML with extensions, preserving LaTeX."""
    text, latex_placeholders = _protect_latex(text)
    text = _fix_list_spacing(text)
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "attr_list"],
        extension_configs={
            "toc": {"permalink": True, "permalink_class": "anchor-link"},
        },
    )
    html = md.convert(text)
    # Store TOC HTML for sidebar use
    _render_markdown._last_toc = getattr(md, "toc", "")
    html = _restore_latex(html, latex_placeholders)
    return html


def _resolve_wiki_links(html: str, papers_by_arxiv: dict, papers_by_slug: dict) -> str:
    """Replace [[arxiv_id]] or [[slug]] with links to detail pages.

    Both references and lookup keys are version-normalised, so citations
    resolve regardless of which paper version is stored in the archive.
    """
    def replace_link(match):
        ref = match.group(1)
        # Normalise only for the arxiv lookup — a slug may legitimately end in
        # "v2" (e.g. "dcn-v2"), which _clean_arxiv_id would truncate.
        if _clean_arxiv_id(ref) in papers_by_arxiv:
            p = papers_by_arxiv[_clean_arxiv_id(ref)]
            return f'<a href="/reading/{p.year}/{p.slug}/" class="wiki-link">{p.model_name or p.title}</a>'
        if ref in papers_by_slug:
            p = papers_by_slug[ref]
            return f'<a href="/reading/{p.year}/{p.slug}/" class="wiki-link">{p.model_name or p.title}</a>'
        return match.group(0)  # Leave unresolved as-is

    return re.sub(r"\[\[([^\]]+)\]\]", replace_link, html)


def _load_papers_enriched():
    """Load all deeply-read papers with computed fields for display.

    Filters out brief-only papers (those without reading.md).
    """
    papers = list_papers()
    enriched = []
    for p in papers:
        has_reading = (PAPERS_DIR / str(p.year) / p.slug / "reading.md").exists()
        if not has_reading:
            continue  # Skip brief-only papers
        company = _normalize_company(p.affiliations)
        enriched.append({
            "meta": p,
            "company": company,
            "has_reading": True,
        })
    return enriched


# Build lookup dicts for wiki links
def _build_paper_lookups(papers):
    """Index papers for [[…]] resolution. Arxiv keys are version-normalised so
    a citation resolves regardless of which version got archived."""
    by_arxiv = {}
    by_slug = {}
    for item in papers:
        p = item["meta"]
        if p.arxiv_id:
            by_arxiv[_clean_arxiv_id(p.arxiv_id)] = p
        by_slug[p.slug] = p
    return by_arxiv, by_slug


def _clean_arxiv_id(aid: str) -> str:
    """Strip version suffix from arxiv ID."""
    return aid.split("v")[0] if "v" in aid and aid.split("v")[-1].isdigit() else aid


def _build_related_index(all_papers_enriched: list):
    """Pre-compute the (graph, dag_node_to_paper) pair used by _get_related_papers.

    Called once per build so the per-paper loop avoids repeated load_graph()
    disk reads and dictionary rebuilds. Also reusable in dev-server handlers
    via an lru-cache wrapper if needed later.
    """
    graph = load_graph()
    dag_node_to_paper = {}
    for item in all_papers_enriched:
        p = item["meta"]
        mn = (p.model_name or "").strip()
        if not mn or mn not in graph.nodes:
            continue
        dag_node_to_paper[mn] = {
            "model_name": mn,
            "title": p.title,
            "year": p.year,
            "slug": p.slug,
        }
    return graph, dag_node_to_paper


def _get_related_papers(paper_meta, all_papers_enriched: list, *, index=None) -> dict:
    """Get related papers from model iteration graph comparison edges.

    A comparison edge (source → target) means target outperforms source.
    Returns {"outperforms": [...], "outperformed_by": [...]} where each item
    is a dict with model_name, title, year, slug. Baseline models that have
    no archived paper get slug="" so the template renders them as plain text.

    ``index`` may be a precomputed tuple from ``_build_related_index`` to
    avoid redundant graph loads when called in a loop.
    """
    # Authoritative match is by model_name. graph.paper_id on baseline nodes
    # historically inherited the citing paper's id and can't be trusted here.
    graph, dag_node_to_paper = index or _build_related_index(all_papers_enriched)

    my_model = (paper_meta.model_name or "").strip()
    if not my_model or my_model not in graph.nodes:
        return {"outperforms": [], "outperformed_by": []}

    def _info_for(name: str) -> dict:
        if name in dag_node_to_paper:
            return dag_node_to_paper[name]
        return {"model_name": name, "title": "", "year": 0, "slug": ""}

    outperforms = []
    outperformed_by = []
    seen_out = set()
    seen_by = set()
    for e in graph.edges:
        if e.target == my_model and e.source != my_model:
            if e.source not in seen_out:
                seen_out.add(e.source)
                outperforms.append(_info_for(e.source))
        elif e.source == my_model and e.target != my_model:
            if e.target not in seen_by:
                seen_by.add(e.target)
                outperformed_by.append(_info_for(e.target))

    return {"outperforms": outperforms, "outperformed_by": outperformed_by}


@reading_bp.route("/")
def paper_list():
    """Paper list page with filters. Supports ?type=papers|digests."""
    doc_type = request.args.get("type", "papers")
    papers = _load_papers_enriched()

    # Always load digest counts for sub-tab badges
    from archivist.web.data import load_docs, prepare_digests_data
    doc_count = len(load_docs(DOCS_DIR))
    digests = prepare_digests_data()
    digest_count = len(digests["daily"]) + len(digests["weekly"]) + len(digests["monthly"])

    if doc_type == "digests":
        return render_template(
            "reading/index.html",
            doc_type="digests",
            total=len(papers),
            digest_count=digest_count,
            doc_count=doc_count,
            daily=digests["daily"],
            weekly=digests["weekly"],
            monthly=digests["monthly"],
        )

    # Default papers view — categories are fixed (always show all 4 types)
    categories = ["generative-rec", "discriminative-rec", "llm", "other"]
    companies = sorted({p["company"] for p in papers if p["company"]})
    from archivist.services.tag_registry import load_whitelist
    tag_list = sorted(load_whitelist())

    sort_by = request.args.get("sort", "published_date")
    if sort_by == "reading_score":
        papers.sort(key=lambda p: p["meta"].reading_score, reverse=True)
    elif sort_by == "score":
        papers.sort(key=lambda p: p["meta"].score, reverse=True)
    elif sort_by == "rating":
        papers.sort(key=lambda p: p["meta"].rating or 0, reverse=True)
    elif sort_by == "date_added":
        papers.sort(key=lambda p: p["meta"].date_added or "", reverse=True)
    elif sort_by == "published_date":
        papers.sort(key=lambda p: p["meta"].published_date or "", reverse=True)

    return render_template(
        "reading/index.html",
        doc_type="papers",
        papers=papers,
        categories=categories,
        companies=companies,
        tag_list=tag_list,
        sort_by=sort_by,
        total=len(papers),
        digest_count=digest_count,
        doc_count=doc_count,
        latest_daily=digests["daily"][0] if digests["daily"] else None,
    )


@reading_bp.route("/digest/<period_type>/<digest_id>/")
def digest_detail(period_type: str, digest_id: str):
    """Digest detail page."""
    from archivist.web.data import render_digest_html
    meta, html = render_digest_html(period_type, digest_id)
    if not meta:
        abort(404)
    return render_template(
        "reading/digest_detail.html",
        meta=meta,
        content=html,
    )


@reading_bp.route("/<int:year>/<slug>/")
def paper_detail(year: int, slug: str):
    """Paper detail page."""
    paper = get_paper(slug)
    if not paper:
        abort(404)

    paper_dir = get_paper_dir(slug)
    if not paper_dir:
        abort(404)

    reading_file = paper_dir / "reading.md"
    if not reading_file.exists():
        abort(404)

    # Render markdown
    md_text = reading_file.read_text(encoding="utf-8")
    html_content = _render_markdown(md_text)
    toc_html = getattr(_render_markdown, "_last_toc", "")

    # Resolve wiki links
    all_papers = _load_papers_enriched()
    by_arxiv, by_slug = _build_paper_lookups(all_papers)
    html_content = _resolve_wiki_links(html_content, by_arxiv, by_slug)

    company = _normalize_company(paper.affiliations)

    # Get related papers from model iteration graph
    related = _get_related_papers(paper, all_papers)

    # Get digests this paper appears in
    from archivist.web.data import build_paper_to_digests_index
    paper_to_digests = build_paper_to_digests_index()
    clean_aid = _clean_arxiv_id(paper.arxiv_id or "")
    appears_in = paper_to_digests.get(clean_aid, [])

    return render_template(
        "reading/detail.html",
        paper=paper,
        company=company,
        content=html_content,
        toc=toc_html,
        related=related,
        appears_in=appears_in,
    )


@reading_bp.route("/<int:year>/<slug>/pdf")
def paper_pdf(year: int, slug: str):
    """Redirect legacy PDF links to ArXiv without serving archived files."""
    paper = get_paper(slug)
    if not paper or paper.year != year:
        abort(404)
    if not paper.arxiv_id:
        abort(410)
    # Accept both modern and pre-2007 IDs, retaining optional versions. The
    # destination is always ArXiv; metadata cannot supply a redirect URL.
    if not re.fullmatch(
        r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v[1-9]\d*)?",
        paper.arxiv_id,
    ):
        abort(404)
    return redirect(f"https://arxiv.org/pdf/{paper.arxiv_id}", code=302)


@reading_bp.route("/<int:year>/<slug>/figures/<path:filename>")
def paper_figure(year: int, slug: str, filename: str):
    """Serve paper figures."""
    paper_dir = get_paper_dir(slug)
    if not paper_dir:
        abort(404)
    fig_file = paper_dir / "figures" / filename
    if not fig_file.exists():
        abort(404)
    return send_file(fig_file)
