"""Read-only MCP server exposing the archivist library to other projects.

Run as `archivist-mcp` (stdio). Other Claude Code projects register it via
`~/.claude.json` and gain `search_papers`, `get_paper_reading`, etc. as tools.
Set `$ARCHIVIST_ROOT` to point at the archive project root; falls back to
auto-detection from this file's location (see `archivist.config`).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from archivist.models import PaperMeta
from archivist.services import dag as dag_svc
from archivist.services import digest as digest_svc
from archivist.services import doc_store
from archivist.services import paper_store
from archivist.services import tag_registry

mcp = FastMCP("archivist")


_REDACTED_PAPER_FIELDS = frozenset({"score_reason", "reading_score_reason", "notes", "rating_reason"})


def _public_paper_dict(paper: PaperMeta, include_internal: bool = False) -> dict[str, Any]:
    """Strip internal fields from a paper meta dict by default."""
    d = paper.to_dict()
    if not include_internal:
        for k in _REDACTED_PAPER_FIELDS:
            d.pop(k, None)
    return d


def _paper_summary(paper: PaperMeta) -> dict[str, Any]:
    """Compact summary for search/list results — keeps token cost low."""
    return {
        "slug": paper.slug,
        "title": paper.title,
        "authors": paper.authors,
        "year": paper.year,
        "tags": paper.tags,
        "category": paper.category,
        "reading_score": paper.reading_score,
        "score": paper.score,
        "deeply_read": paper.deeply_read,
        "one_line_summary": paper.one_line_summary,
        "arxiv_id": paper.arxiv_id,
        "url": paper.url,
    }


def _paper_loaded(paper: PaperMeta, has_reading_report: bool) -> dict[str, Any]:
    """Richer summary for `load_papers` — bilingual summaries + tier-2 availability hint."""
    return {
        "slug": paper.slug,
        "title": paper.title,
        "authors": paper.authors,
        "year": paper.year,
        "published_date": paper.published_date,
        "tags": paper.tags,
        "category": paper.category,
        "model_name": paper.model_name,
        "arxiv_id": paper.arxiv_id,
        "url": paper.url,
        "one_line_summary": paper.one_line_summary,
        "one_line_summary_en": paper.one_line_summary_en,
        "score": paper.score,
        "reading_score": paper.reading_score,
        "deeply_read": paper.deeply_read,
        "has_reading_report": has_reading_report,
    }


def _score_paper(query: str, paper: PaperMeta) -> int:
    """Substring-match score across paper fields. 0 = no match."""
    if not query:
        return 1
    q = query.lower()
    score = 0
    if q in paper.title.lower():
        score += 5
    for tag in paper.tags:
        if q in tag.lower():
            score += 3
    if q in paper.one_line_summary.lower() or q in paper.one_line_summary_en.lower():
        score += 2
    if q in (paper.model_name or "").lower():
        score += 4
    for author in paper.authors:
        if q in author.lower():
            score += 1
            break
    if q in paper.abstract.lower():
        score += 1
    return score


def _score_doc(query: str, meta, content: str = "") -> int:
    if not query:
        return 1
    q = query.lower()
    score = 0
    if q in meta.title.lower():
        score += 5
    for tag in meta.tags:
        if q in tag.lower():
            score += 3
    if q in (meta.description or "").lower():
        score += 2
    if content and q in content.lower():
        score += 1
    return score


# ── Tools ───────────────────────────────────────────────────────


@mcp.tool()
def search_papers(
    query: str = "",
    tag: str | None = None,
    deeply_read_only: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search papers in the archive by substring match across title, tags, model name,
    summary, authors, and abstract. Empty query returns most-recent papers.

    Args:
        query: Substring to match (case-insensitive). Empty = no filter.
        tag: Filter to papers carrying this tag.
        deeply_read_only: If True, only return papers with deeply_read=True.
        limit: Max number of results (default 20).

    Returns: list of compact paper summaries, sorted by match score then reading_score.
    """
    papers = paper_store.list_papers(tag=tag)
    if deeply_read_only:
        papers = [p for p in papers if p.deeply_read]

    scored = [(_score_paper(query, p), p) for p in papers]
    scored = [(s, p) for s, p in scored if s > 0]
    scored.sort(key=lambda sp: (-sp[0], -sp[1].reading_score, sp[1].date_added), reverse=False)
    return [_paper_summary(p) for _, p in scored[:limit]]


@mcp.tool()
def get_paper(slug: str, include_internal: bool = False) -> dict[str, Any] | None:
    """Get full metadata for a single paper by slug.

    Args:
        slug: The paper slug (from search_papers results).
        include_internal: If True, include private fields (score_reason, reading_score_reason,
            notes, rating_reason). Default False keeps the response public-safe.

    Returns: paper meta dict + has_reading_report flag, or None if not found.
    """
    paper = paper_store.get_paper(slug)
    if paper is None:
        return None
    paper_dir = paper_store.get_paper_dir(slug)
    has_reading = bool(paper_dir and (paper_dir / "reading.md").exists())
    out = _public_paper_dict(paper, include_internal=include_internal)
    out["has_reading_report"] = has_reading
    return out


@mcp.tool()
def get_paper_reading(slug: str) -> dict[str, Any] | None:
    """Get the full deep-reading report (reading.md) for a paper.

    Args:
        slug: The paper slug.

    Returns: {slug, title, markdown} or None if no reading report exists.
    """
    paper = paper_store.get_paper(slug)
    paper_dir = paper_store.get_paper_dir(slug)
    if not paper or not paper_dir:
        return None
    reading_file = paper_dir / "reading.md"
    if not reading_file.exists():
        return None
    return {
        "slug": slug,
        "title": paper.title,
        "markdown": reading_file.read_text(encoding="utf-8"),
    }


@mcp.tool()
def search_docs(query: str = "", tag: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Search internal documents by substring match across title, tags, description, and content.

    Args:
        query: Substring to match (case-insensitive). Empty = no filter.
        tag: Filter to docs carrying this tag.
        limit: Max results.

    Returns: list of {slug, title, description, tags, category, date_modified}.
    """
    docs = doc_store.list_docs(tag=tag)
    results = []
    for meta in docs:
        # Only load content for scoring if query is set
        content = ""
        if query:
            loaded = doc_store.get_doc(meta.slug)
            content = loaded[1] if loaded else ""
        score = _score_doc(query, meta, content)
        if score > 0:
            results.append((score, meta))
    results.sort(key=lambda sm: (-sm[0], sm[1].date_modified), reverse=False)
    return [
        {
            "slug": m.slug,
            "title": m.title,
            "description": m.description,
            "tags": m.tags,
            "category": m.category,
            "date_modified": m.date_modified,
        }
        for _, m in results[:limit]
    ]


@mcp.tool()
def get_doc(slug: str) -> dict[str, Any] | None:
    """Get a single internal document's full content.

    Args:
        slug: The document slug.

    Returns: {meta: {...}, content: str} or None if not found.
    """
    result = doc_store.get_doc(slug)
    if result is None:
        return None
    meta, content = result
    return {"meta": meta.to_dict(), "content": content}


@mcp.tool()
def list_tags() -> dict[str, Any]:
    """List the tag whitelist used across the archive.

    Returns: {tags: sorted list of allowed tags, count: int}.
    """
    whitelist = tag_registry.load_whitelist()
    tags = sorted(whitelist)
    return {"tags": tags, "count": len(tags)}


@mcp.tool()
def search_models(query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Search the model DAG by substring match on model name or paper title.

    Args:
        query: Substring (case-insensitive). Empty = list all nodes.
        limit: Max results.

    Returns: list of {model_name, paper_id, paper_title, description, category}.
    """
    graph = dag_svc.load_graph()
    q = query.lower()
    matches = []
    for node in graph.nodes.values():
        if not q or q in node.model_name.lower() or q in node.paper_title.lower():
            matches.append(node)
    matches.sort(key=lambda n: n.model_name.lower())
    return [n.to_dict() for n in matches[:limit]]


@mcp.tool()
def get_model(name: str) -> dict[str, Any] | None:
    """Get a model's full DAG entry: node, citations, and comparison edges.

    Args:
        name: Exact model name (case-sensitive, see search_models).

    Returns: {node, cites, cited_by, superior_to, inferior_to} or None.
        - cites/cited_by: model names from citation edges
        - superior_to: edges where this model is the target (better than source)
        - inferior_to: edges where this model is the source (worse than target)
    """
    graph = dag_svc.load_graph()
    node = graph.nodes.get(name)
    if node is None:
        return None
    cites, cited_by = dag_svc.get_model_citations(graph, name)
    superior_to = [e.to_dict() for e in graph.edges if e.target == name]
    inferior_to = [e.to_dict() for e in graph.edges if e.source == name]
    return {
        "node": node.to_dict(),
        "cites": cites,
        "cited_by": cited_by,
        "superior_to": superior_to,
        "inferior_to": inferior_to,
    }


@mcp.tool()
def list_digests(period: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """List recent digest reports (daily/weekly/monthly).

    Args:
        period: "daily" | "weekly" | "monthly", or None for all.
        limit: Max results.

    Returns: list of digest summaries (period_type, id, title, period_start/end, theme).
    """
    if period and period not in ("daily", "weekly", "monthly"):
        return []
    digests = digest_svc.load_digests(period_type=period)
    return [
        {
            "period_type": d.period_type,
            "id": d.id,
            "title": d.title,
            "period_start": d.period_start,
            "period_end": d.period_end,
            "paper_count": d.paper_count,
            "deeply_read_count": d.deeply_read_count,
            "theme": d.theme,
            "theme_tags": d.theme_tags,
        }
        for d in digests[:limit]
    ]


@mcp.tool()
def get_digest(period: str, digest_id: str) -> dict[str, Any] | None:
    """Get a single digest's metadata and full markdown body.

    Args:
        period: "daily" | "weekly" | "monthly".
        digest_id: The digest ID (e.g. "2026-04-09" / "2026-W15" / "2026-04").

    Returns: {meta, markdown} or None if not found.
    """
    if period not in ("daily", "weekly", "monthly"):
        return None
    meta, body = digest_svc.load_digest(period, digest_id)
    if meta is None:
        return None
    return {"meta": meta.to_dict(), "markdown": body}


_VALID_SORTS = ("reading_score", "date_added", "score")


@mcp.tool()
def load_papers(
    tags: list[str] | None = None,
    category: str | None = None,
    year: int | None = None,
    deeply_read_only: bool = False,
    sort: str = "reading_score",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Tier-1 batch loader: pull a topical slice of papers into context.

    Use this when you want to survey what the archive has on a topic before
    deciding which paper(s) to deepen on. Returns richer summaries than
    search_papers (bilingual one-liners + has_reading_report flag for tier-2
    triage). For keyword search use search_papers instead.

    Progressive disclosure: this is tier 1 (cheap, batch). Tier 2 = call
    get_paper_reading(slug) for the deep-read report. Tier 3 = call
    get_paper_pdf(slug) only if the reading report doesn't answer your question.

    Args:
        tags: Filter to papers carrying ALL of these tags (intersection).
            Use list_tags to see the whitelist.
        category: One of "discriminative-rec", "generative-rec", "llm", "other".
        year: e.g. 2026.
        deeply_read_only: If True, exclude shallow/brief papers (no reading report).
        sort: "reading_score" (default, agent-judged depth) | "date_added" (newest first)
            | "score" (LLM triage score from digest pipeline). All descending.
        limit: Max results (default 20).

    Returns: list of paper dicts with bilingual summaries, scores, and
        has_reading_report flag indicating whether tier-2 is available.
    """
    if sort not in _VALID_SORTS:
        sort = "reading_score"

    base_tag = tags[0] if tags else None
    papers = paper_store.list_papers(tag=base_tag, year=year, category=category)

    if tags and len(tags) > 1:
        rest = set(tags[1:])
        papers = [p for p in papers if rest.issubset(set(p.tags))]
    if deeply_read_only:
        papers = [p for p in papers if p.deeply_read]

    if sort == "reading_score":
        papers.sort(key=lambda p: (p.reading_score, p.date_added), reverse=True)
    elif sort == "score":
        papers.sort(key=lambda p: (p.score, p.date_added), reverse=True)
    # date_added: list_papers already returns newest first

    out = []
    for p in papers[:limit]:
        paper_dir = paper_store.get_paper_dir(p.slug)
        has_report = bool(paper_dir and (paper_dir / "reading.md").exists())
        out.append(_paper_loaded(p, has_report))
    return out


@mcp.tool()
def get_paper_pdf(slug: str) -> dict[str, Any] | None:
    """Tier-3 escape hatch: resolve a paper's PDF path so you can read it directly.

    USAGE: this tool returns the PDF's filesystem path. To read its contents
    you MUST use Claude Code's built-in Read tool with the `pages` argument
    (e.g. Read(file_path=pdf_path, pages="5-8")). Do NOT extract text via
    pymupdf, pdfplumber, or any other library — Read handles PDFs natively
    and is the only sanctioned path here.

    Reach for this only when get_paper_reading didn't answer your question
    (e.g. you need a specific algorithm, table, or figure caption verbatim).
    Brief papers have no archived PDF and return pdf_path=None.

    Args:
        slug: The paper slug (from search_papers / load_papers).

    Returns: {slug, title, pdf_path, page_count, size_bytes, read_hint} on
        success; {slug, pdf_path: None, reason} for brief / missing papers;
        None if the slug is not in the archive at all.
    """
    paper = paper_store.get_paper(slug)
    if paper is None:
        return None
    paper_dir = paper_store.get_paper_dir(slug)
    if paper_dir is None:
        return None
    pdf_path = paper_dir / "document.pdf"
    if not pdf_path.exists():
        return {
            "slug": slug,
            "title": paper.title,
            "pdf_path": None,
            "reason": "brief paper, no PDF archived",
        }

    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        page_count = len(doc)

    return {
        "slug": slug,
        "title": paper.title,
        "pdf_path": str(pdf_path),
        "page_count": page_count,
        "size_bytes": pdf_path.stat().st_size,
        "read_hint": "Use Claude Code's Read tool with this path and a `pages` arg "
                     "(e.g. pages='5-8') to extract specific pages. Do not use any "
                     "other PDF parsing library.",
    }


def main() -> None:
    """Entry point for `archivist-mcp` script. Runs the FastMCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
