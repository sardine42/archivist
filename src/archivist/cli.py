"""Archivist CLI - filesystem-based document archive."""

import json
import os
import subprocess
import sys
from pathlib import Path

import click

from archivist.config import (
    ARCHIVE_ROOT,
    PAPERS_DIR,
    PAPERS_BRIEF_DIR,
    PROJECT_ROOT,
    ensure_archive_dirs,
    get_deploy_settings,
)
from archivist.services import doc_store, paper_store


def _check_tags_or_exit(tag_list: list[str], allow_new: tuple[str, ...]) -> None:
    """Reject unknown tags unless they're in --allow-new-tag overrides."""
    from archivist.services.tag_registry import suggest_similar, validate_tags
    _, unknown = validate_tags(tag_list)
    really_unknown = [t for t in unknown if t not in allow_new]
    if not really_unknown:
        return
    lines = [f"Unknown tag(s): {', '.join(really_unknown)}"]
    for t in really_unknown:
        sims = suggest_similar(t)
        if sims:
            lines.append(f"  {t!r}: did you mean {', '.join(sims)}?")
    lines.append(
        "Pass --allow-new-tag <tag> to override (use sparingly; prefer "
        "`archivist tag promote` after the LLM has proposed it)."
    )
    click.echo("\n".join(lines), err=True)
    sys.exit(1)


@click.group()
def cli():
    """Archivist - manage your research papers and documents."""
    pass


@cli.command()
def init():
    """Initialize the archive directory structure."""
    ensure_archive_dirs()
    click.echo(f"Archive initialized at {ARCHIVE_ROOT}/")


# ── Paper commands ──────────────────────────────────────────


@cli.group("paper")
def paper_group():
    """Manage research papers."""
    pass


@paper_group.command("import")
@click.argument("pdf", type=click.Path(exists=True, path_type=Path))
@click.option("--title", "-t", help="Paper title (default: extracted from PDF)")
@click.option("--tags", help="Comma-separated tags")
@click.option("--allow-new-tag", multiple=True,
              help="Whitelist override: accept this tag even if not in config.yaml. "
                   "Repeatable. Use sparingly; prefer the proposed_tags pipeline.")
@click.option("--category", "-c", default="other", help="Comma-separated categories: generative-rec / discriminative-rec / llm / other (可多选，如 generative-rec,discriminative-rec)")
def paper_import(pdf: Path, title: str | None, tags: str | None, allow_new_tag: tuple[str, ...], category: str):
    """Import a PDF paper into the archive."""
    ensure_archive_dirs()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    if tag_list:
        _check_tags_or_exit(tag_list, allow_new_tag)
    cat_list = [c.strip() for c in category.split(",") if c.strip()] or ["other"]
    meta = paper_store.import_paper(pdf, title=title, tags=tag_list, category=cat_list)
    click.echo(f"Imported: {meta.title}")
    click.echo(f"  slug: {meta.slug}")
    click.echo(f"  year: {meta.year}")
    click.echo(f"  authors: {', '.join(meta.authors) or '(none)'}")
    click.echo(f"  tags: {', '.join(meta.tags) or '(none)'}")


@paper_group.command("list")
@click.option("--tag", help="Filter by tag")
@click.option("--year", type=int, help="Filter by year")
@click.option("--status", help="Filter by read status: unread/reading/read")
@click.option("--category", "-c", help="Filter by single category (论文含此 category 即列出)")
def paper_list(tag: str | None, year: int | None, status: str | None, category: str | None):
    """List papers in the archive."""
    papers = paper_store.list_papers(tag=tag, year=year, status=status, category=category)
    if not papers:
        click.echo("No papers found.")
        return
    for p in papers:
        tags_str = f" [{', '.join(p.tags)}]" if p.tags else ""
        status_icon = {"unread": "○", "reading": "◐", "read": "●"}.get(p.read_status, "?")
        click.echo(f"  {status_icon} {p.year}/{p.slug} — {p.title}{tags_str}")
    click.echo(f"\n{len(papers)} paper(s)")


@paper_group.command("show")
@click.argument("slug")
def paper_show(slug: str):
    """Show details of a paper."""
    paper = paper_store.get_paper(slug)
    if not paper:
        click.echo(f"Paper not found: {slug}", err=True)
        sys.exit(1)

    click.echo(f"Title:      {paper.title}")
    if paper.model_name:
        click.echo(f"Model:      {paper.model_name}")
    click.echo(f"Slug:       {paper.slug}")
    click.echo(f"Year:       {paper.year}")
    click.echo(f"Authors:    {', '.join(paper.authors) or '(none)'}")
    click.echo(f"Category:   {', '.join(paper.category) or '(none)'}")
    click.echo(f"Tags:       {', '.join(paper.tags) or '(none)'}")
    click.echo(f"Status:     {paper.read_status}")
    click.echo(f"Score:      {paper.score} (abstract)")
    if paper.reading_score:
        click.echo(f"ReadScore:  {paper.reading_score} (deep read)")
    if paper.rating:
        click.echo(f"Rating:     {paper.rating}/10 (human)")
    if paper.one_line_summary:
        click.echo(f"Summary:    {paper.one_line_summary}")
    if paper.arxiv_id:
        click.echo(f"Arxiv:      {paper.arxiv_id}")
    if paper.published_date:
        click.echo(f"Published:  {paper.published_date}")
    if paper.url:
        click.echo(f"URL:        {paper.url}")
    if paper.notes:
        click.echo(f"Notes:      {paper.notes}")
    click.echo(f"Added:      {paper.date_added}")


@paper_group.command("edit")
@click.argument("slug")
@click.option("--tags", help="Set tags (comma-separated). Validated against config.yaml whitelist.")
@click.option("--proposed-tags", help="Set LLM-proposed tags awaiting whitelist review (comma-separated). "
                                     "Bypasses whitelist validation by design.")
@click.option("--allow-new-tag", multiple=True,
              help="Accept this tag even if not in whitelist (repeatable). "
                   "Prefer the proposed_tags pipeline + `archivist tag promote`.")
@click.option("--status", help="Set read status: unread/reading/read")
@click.option("--rating", type=int, help="Set rating (1-10)")
@click.option("--rating-reason", help="Reason/note for the rating (used by /refine-rubric)")
@click.option("--feedback-consumed/--no-feedback-consumed", default=None,
              help="Mark feedback as processed (avoids re-triggering refine-rubric)")
@click.option("--category", "-c", help="Set category (comma-separated, 可多选): generative-rec / discriminative-rec / llm / other")
@click.option("--title", "-t", help="Set title")
@click.option("--model-name", help="Set model name abbreviation")
@click.option("--reading-score", type=float, help="Set reading score (1-10)")
@click.option("--published-date", help="Set published date (YYYY-MM-DD)")
@click.option("--url", help="Set ArXiv URL")
def paper_edit(slug: str, tags: str | None, proposed_tags: str | None,
               allow_new_tag: tuple[str, ...],
               status: str | None, rating: int | None,
               rating_reason: str | None, feedback_consumed: bool | None,
               category: str | None, title: str | None, model_name: str | None,
               reading_score: float | None,
               published_date: str | None, url: str | None):
    """Edit paper metadata (writes to meta.json).

    Feedback note: setting --rating alone (with or without --rating-reason) only
    stores the data. CLI does not trigger criteria refinement. To iterate criteria
    based on feedback, use the /refine-rubric skill in a Claude Code session.
    """
    kwargs = {}
    if tags is not None:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            _check_tags_or_exit(tag_list, allow_new_tag)
        kwargs["tags"] = tag_list
    if proposed_tags is not None:
        kwargs["proposed_tags"] = [t.strip() for t in proposed_tags.split(",") if t.strip()]
    if status is not None:
        kwargs["read_status"] = status
    if rating is not None:
        if not 1 <= rating <= 10:
            click.echo("Rating must be 1-10", err=True)
            sys.exit(1)
        kwargs["rating"] = rating
        # New rating auto-resets feedback_consumed unless caller overrides.
        if feedback_consumed is None:
            kwargs["feedback_consumed"] = False
    if rating_reason is not None:
        kwargs["rating_reason"] = rating_reason
    if feedback_consumed is not None:
        kwargs["feedback_consumed"] = feedback_consumed
    if category is not None:
        cat_list = [c.strip() for c in category.split(",") if c.strip()]
        kwargs["category"] = cat_list or ["other"]
    if title is not None:
        kwargs["title"] = title
    if model_name is not None:
        kwargs["model_name"] = model_name
    if reading_score is not None:
        if not 1 <= reading_score <= 10:
            click.echo("Reading score must be 1-10", err=True)
            sys.exit(1)
        kwargs["reading_score"] = reading_score
    if published_date is not None:
        kwargs["published_date"] = published_date
    if url is not None:
        kwargs["url"] = url

    if not kwargs:
        click.echo("No changes specified.")
        return

    paper = paper_store.update_paper(slug, **kwargs)
    if not paper:
        click.echo(f"Paper not found: {slug}", err=True)
        sys.exit(1)
    click.echo(f"Updated: {paper.title}")


@paper_group.command("apply-reading")
@click.argument("data_file", type=click.Path(exists=True, path_type=Path))
def paper_apply_reading(data_file: Path):
    """Apply a deep-read result: updates meta, benchmarks, DAG, and progress.

    Called by the read-paper skill after finishing a deep read. See
    .claude/skills/read-paper/update-data-schema.md for the data.json schema.
    """
    from archivist.services.reading_apply import apply_reading

    data = json.loads(data_file.read_text())
    summary = apply_reading(data)

    click.echo(f"[meta] Updated {summary['slug']}")
    if summary["benchmarks_added"]:
        click.echo(f"[benchmark] Added {summary['benchmarks_added']} entries, "
                   f"{len(summary['benchmark_conflicts'])} conflicts")
        for c in summary["benchmark_conflicts"]:
            click.echo(f"  CONFLICT: {c[:100]}...")
    if "dag_model" in summary:
        click.echo(f"[dag] Registered {summary['dag_model']}, "
                   f"{summary['dag_new_citations']} citation edges, "
                   f"{summary['dag_edges_added']} comparison edges")
        for c in summary["dag_conflicts"]:
            click.echo(f"  DAG CONFLICT: {c[:100]}...")
    if summary.get("progress_updated"):
        click.echo(f"[progress] {summary['arxiv_id']} -> done")
    click.echo(f"\nAll updates complete for {summary['arxiv_id']}")


@paper_group.command("backfill")
@click.option("--field", "-f", multiple=True, required=True, help="Field(s) to check/backfill")
@click.option("--dry-run", is_flag=True, help="Only show missing fields, don't prompt")
def paper_backfill(field: tuple[str], dry_run: bool):
    """List papers with missing fields for agent backfill.

    Scans all papers and reports which ones are missing the specified fields.
    Run in a Claude Code session so the agent can read papers and fill values.
    """
    papers = paper_store.list_papers()
    if not papers:
        click.echo("No papers found.")
        return

    missing = []
    complete = 0
    for p in papers:
        empty_fields = []
        for f in field:
            val = getattr(p, f, None)
            if not val:
                empty_fields.append(f)
        if empty_fields:
            missing.append((p, empty_fields))
        else:
            complete += 1

    click.echo(f"Fields: {', '.join(field)}")
    click.echo(f"Complete: {complete}/{len(papers)}  Missing: {len(missing)}/{len(papers)}")
    click.echo()

    if not missing:
        click.echo("All papers have the specified fields filled.")
        return

    for p, empty_fields in missing:
        click.echo(f"  {p.year}/{p.slug} — {p.title[:60]}")
        click.echo(f"    missing: {', '.join(empty_fields)}")

    if dry_run:
        click.echo(f"\nDry run: {len(missing)} paper(s) need backfill.")
        click.echo("Use `archivist paper edit <slug> --model-name ... --reading-score ...` to fill.")


@paper_group.command("note")
@click.argument("slug")
def paper_note(slug: str):
    """Edit paper notes with $EDITOR."""
    paper_dir = paper_store.get_paper_dir(slug)
    if not paper_dir:
        click.echo(f"Paper not found: {slug}", err=True)
        sys.exit(1)

    notes_file = paper_dir / "notes.md"
    if not notes_file.exists():
        notes_file.write_text("", encoding="utf-8")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(notes_file)], check=True)
    click.echo(f"Notes saved: {notes_file}")


@paper_group.command("open")
@click.argument("slug")
def paper_open(slug: str):
    """Open paper PDF with system viewer."""
    paper_dir = paper_store.get_paper_dir(slug)
    if not paper_dir:
        click.echo(f"Paper not found: {slug}", err=True)
        sys.exit(1)

    pdf_path = paper_dir / "document.pdf"
    if not pdf_path.exists():
        click.echo(f"PDF not found: {pdf_path}", err=True)
        sys.exit(1)

    if sys.platform == "darwin":
        subprocess.run(["open", str(pdf_path)])
    elif sys.platform == "win32":
        os.startfile(str(pdf_path))
    else:
        subprocess.run(["xdg-open", str(pdf_path)])


@paper_group.command("remove")
@click.argument("slug")
@click.confirmation_option(prompt="Are you sure you want to remove this paper?")
def paper_remove(slug: str):
    """Remove a paper from the archive."""
    if paper_store.remove_paper(slug):
        click.echo(f"Removed: {slug}")
    else:
        click.echo(f"Paper not found: {slug}", err=True)
        sys.exit(1)


# ── DAG commands ────────────────────────────────────────────


@cli.group("dag")
def dag_group():
    """Inspect the model graph (DAG)."""
    pass


@dag_group.command("list-nodes")
def dag_list_nodes():
    """List all model nodes in the DAG (for baseline-name lookup before registering edges)."""
    from archivist.services.dag import load_graph
    graph = load_graph()
    names = sorted(graph.nodes.keys())
    click.echo(f"现有 DAG 节点 ({len(names)} 个):")
    for n in names:
        click.echo(f"  {n}")


# ── Rubric (scoring feedback) commands ──────────────────────


@cli.group("rubric")
def rubric_group():
    """Inspect scoring feedback (read-only).

    Criteria updates are handled by the /refine-rubric skill in a Claude Code
    session — this group only surfaces the data.
    """
    pass


@rubric_group.command("list-pending")
@click.option("--format", "fmt", default="table",
              type=click.Choice(["table", "json"]),
              help="Output format")
def rubric_list_pending(fmt: str):
    """List papers with rating feedback that hasn't been processed yet.

    Filter: rating set, feedback_consumed != true, rating != auto_score.
    """
    from archivist.services.feedback import collect_corrections

    corrections = collect_corrections()
    if fmt == "json":
        click.echo(json.dumps([c.to_dict() for c in corrections],
                              ensure_ascii=False, indent=2))
        return

    if not corrections:
        click.echo("No pending rating feedback. All ratings match auto-score or are already processed.")
        return

    click.echo(f"Pending rating feedback ({len(corrections)} papers):\n")
    for c in corrections:
        stage = "精读" if c.deeply_read else "摘要"
        click.echo(f"  [{stage}] {c.slug}")
        click.echo(f"    title:     {c.title[:80]}")
        click.echo(f"    auto={c.auto_score:.1f}  rating={c.rating}  deviation={c.deviation:+.1f}")
        if c.rating_reason:
            click.echo(f"    reason:    {c.rating_reason[:120]}")
        click.echo()


# ── Tag whitelist governance ────────────────────────────────


@cli.group("tag")
def tag_group():
    """Govern the tag whitelist (config.yaml).

    LLM-proposed tags accumulate in each paper's `proposed_tags` field.
    Use this group to triage them: promote good ones into the active
    whitelist, alias synonyms onto existing tags, or reject noise.
    """
    pass


@tag_group.command("list-pending")
@click.option("--threshold", type=int, default=3,
              help="Mark tags with ≥N papers as ready-to-promote (default 3)")
@click.option("--format", "fmt", default="table",
              type=click.Choice(["table", "json"]), help="Output format")
def tag_list_pending(threshold: int, fmt: str):
    """List LLM-proposed tags awaiting human review."""
    from archivist.services.tag_pending import collect_pending

    pending = collect_pending(threshold=threshold)
    if fmt == "json":
        click.echo(json.dumps([p.to_dict() for p in pending],
                              ensure_ascii=False, indent=2))
        return

    if not pending:
        click.echo("No pending proposed_tags.")
        return

    click.echo(f"Pending proposed tags ({len(pending)} unique):\n")
    for p in pending:
        marker = "  [ready to promote]" if p.ready_to_promote else ""
        click.echo(f"  {p.tag}  ×{p.paper_count}{marker}")
        for slug in p.slugs[:5]:
            click.echo(f"      {slug}")
        if len(p.slugs) > 5:
            click.echo(f"      … and {len(p.slugs) - 5} more")


def _append_tag_to_config(tag: str, gloss: str = "") -> None:
    """Append a new entry to the `tags:` block in config.yaml, preserving comments."""
    from archivist.config import PROJECT_ROOT
    config_path = PROJECT_ROOT / "config.yaml"
    text = config_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Locate the `tags:` block
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "tags:" or ln.startswith("tags:"):
            start = i
            break
    if start is None:
        raise click.ClickException("config.yaml has no top-level `tags:` block")

    # Find last `  - <tag>` line in the block; the block ends when indent drops
    last_item = start
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        stripped = ln.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if ln.startswith("  -"):
            last_item = i
        elif not ln.startswith("  "):
            break  # exited the tags block

    new_line = f"  - {tag}"
    if gloss:
        new_line = f"  - {tag:<22} # {gloss}"
    lines.insert(last_item + 1, new_line)
    config_path.write_text("\n".join(lines), encoding="utf-8")


@tag_group.command("promote")
@click.argument("tag")
@click.option("--gloss", default="", help="Optional one-line Chinese gloss for the tag")
def tag_promote(tag: str, gloss: str):
    """Add `tag` to config.yaml and migrate proposed_tags → tags everywhere."""
    from archivist.services.tag_registry import load_whitelist, reload_whitelist
    from archivist.services.tag_pending import promote_tag

    if tag in load_whitelist():
        click.echo(f"{tag!r} already in whitelist; skipping config edit.")
    else:
        _append_tag_to_config(tag, gloss=gloss)
        reload_whitelist()
        click.echo(f"Added {tag!r} to config.yaml whitelist.")

    updated, _ = promote_tag(tag)
    click.echo(f"Migrated {updated} paper(s): proposed_tags[{tag!r}] → tags[{tag!r}].")


@tag_group.command("alias")
@click.argument("old_tag")
@click.argument("new_tag")
def tag_alias(old_tag: str, new_tag: str):
    """Rewrite proposed_tags[OLD_TAG] → tags[NEW_TAG] across all papers.

    Use when a proposed tag turns out to be a synonym of an existing whitelist tag.
    """
    from archivist.services.tag_pending import alias_tag

    try:
        updated = alias_tag(old_tag, new_tag)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"Rewrote {updated} paper(s): proposed_tags[{old_tag!r}] → tags[{new_tag!r}].")


@tag_group.command("reject")
@click.argument("tag")
@click.confirmation_option(prompt="Drop this proposed tag from all papers?")
def tag_reject(tag: str):
    """Drop a proposed tag from every paper's proposed_tags list."""
    from archivist.services.tag_pending import reject_tag

    updated = reject_tag(tag)
    click.echo(f"Removed {tag!r} from {updated} paper(s).")


# ── Doc commands ────────────────────────────────────────────


@cli.group("doc")
def doc_group():
    """Manage project documents."""
    pass


@doc_group.command("add")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--title", "-t", help="Document title (default: filename)")
@click.option("--tags", help="Comma-separated tags")
@click.option("--category", "-c", default="", help="Category")
@click.option("--description", "-d", default="", help="Short description")
def doc_add(file: Path, title: str | None, tags: str | None, category: str, description: str):
    """Archive a document file."""
    ensure_archive_dirs()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    meta = doc_store.add_doc(file_path=file, title=title, tags=tag_list, category=category, description=description)
    click.echo(f"Archived: {meta.title}")
    click.echo(f"  slug: {meta.slug}")
    click.echo(f"  tags: {', '.join(meta.tags) or '(none)'}")


@doc_group.command("list")
@click.option("--tag", help="Filter by tag")
@click.option("--category", "-c", help="Filter by category")
def doc_list(tag: str | None, category: str | None):
    """List archived documents."""
    docs = doc_store.list_docs(tag=tag, category=category)
    if not docs:
        click.echo("No documents found.")
        return
    for d in docs:
        tags_str = f" [{', '.join(d.tags)}]" if d.tags else ""
        cat_str = f" ({d.category})" if d.category else ""
        click.echo(f"  {d.slug} — {d.title}{cat_str}{tags_str}")
    click.echo(f"\n{len(docs)} document(s)")


@doc_group.command("show")
@click.argument("slug")
def doc_show(slug: str):
    """Show a document's content."""
    result = doc_store.get_doc(slug)
    if not result:
        click.echo(f"Document not found: {slug}", err=True)
        sys.exit(1)
    meta, content = result
    click.echo(f"Title:       {meta.title}")
    click.echo(f"Category:    {meta.category or '(none)'}")
    click.echo(f"Tags:        {', '.join(meta.tags) or '(none)'}")
    click.echo(f"Description: {meta.description or '(none)'}")
    click.echo(f"Created:     {meta.date_created}")
    click.echo("─" * 40)
    click.echo(content)


@doc_group.command("remove")
@click.argument("slug")
@click.confirmation_option(prompt="Are you sure you want to remove this document?")
def doc_remove(slug: str):
    """Remove a document from the archive."""
    if doc_store.remove_doc(slug):
        click.echo(f"Removed: {slug}")
    else:
        click.echo(f"Document not found: {slug}", err=True)
        sys.exit(1)


# ── Arxiv commands ──────────────────────────────────────────


@cli.group("arxiv")
def arxiv_group():
    """Arxiv paper fetching and scoring."""
    pass


@arxiv_group.command("fetch")
@click.option("--date", "-d", help="Fetch papers from date (YYYY-MM-DD). Default: latest.")
@click.option("--from", "date_from", help="Date range start (YYYY-MM-DD)")
@click.option("--to", "date_to", help="Date range end (YYYY-MM-DD)")
@click.option("--categories", help="Comma-separated categories (default: from config)")
def arxiv_fetch(date: str | None, date_from: str | None, date_to: str | None,
                categories: str | None):
    """Fetch papers from Arxiv and pre-filter by whitelist keywords.

    Outputs a candidates JSON file for LLM scoring in the next step.
    """
    from datetime import datetime, timezone
    from archivist.config import load_config
    from archivist.services.arxiv_fetch import fetch_papers
    from archivist.services.arxiv_scorer import pre_filter, candidates_to_json
    from archivist.utils import write_json

    ensure_archive_dirs()
    config = load_config()
    arxiv_cfg = config.get("arxiv", {})

    cat_list = (
        [c.strip() for c in categories.split(",")]
        if categories
        else arxiv_cfg.get("categories", ["cs.IR", "cs.LG"])
    )
    max_results = arxiv_cfg.get("max_results_per_category", 100)

    click.echo(f"Fetching from {', '.join(cat_list)}...")
    if date_from and date_to:
        click.echo(f"Date range: {date_from} ~ {date_to}")
        max_results = 500
    elif date:
        click.echo(f"Date filter: {date}")

    raw_papers = fetch_papers(cat_list, max_results=max_results, date=date,
                              date_from=date_from, date_to=date_to)
    click.echo(f"Fetched {len(raw_papers)} papers")

    if not raw_papers:
        click.echo("No papers found.")
        return

    candidates = pre_filter(raw_papers)
    click.echo(f"Passed keyword filter: {len(candidates)} / {len(raw_papers)} papers")

    if not candidates:
        click.echo("No papers passed the filter.")
        return

    # Save candidates for LLM scoring
    now = datetime.now(timezone.utc)
    digest_date = date or (f"{date_from}~{date_to}" if date_from else now.strftime("%Y-%m-%d"))
    output_file = PAPERS_DIR / f"_candidates_{digest_date}.json"
    write_json(output_file, {
        "date": digest_date,
        "total_fetched": len(raw_papers),
        "total_candidates": len(candidates),
        "candidates": candidates_to_json(candidates),
    })

    click.echo(f"\nCandidates saved to: {output_file}")
    click.echo(f"Next: run `archivist digest run` to score, deep-read top papers, and generate the digest.")


@arxiv_group.command("download")
@click.argument("arxiv_id")
@click.option("--title", "-t", help="Paper title")
def arxiv_download(arxiv_id: str, title: str | None):
    """Download and archive a single paper by arxiv ID."""
    from datetime import datetime, timezone
    from archivist.services.arxiv_fetch import download_pdf
    from archivist.services.pdf_extract import extract_text
    from archivist.models import PaperMeta
    from archivist.utils import generate_id, write_json, write_text
    import time

    ensure_archive_dirs()

    now = datetime.now(timezone.utc)
    year = now.year
    slug = _arxiv_slug(title or arxiv_id)
    paper_dir = PAPERS_DIR / str(year) / slug
    if paper_dir.exists():
        slug = f"{slug}-{arxiv_id.replace('/', '-')}"
        paper_dir = PAPERS_DIR / str(year) / slug
    paper_dir.mkdir(parents=True, exist_ok=True)

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    pdf_path = paper_dir / "document.pdf"

    click.echo(f"Downloading {arxiv_id}...")
    try:
        download_pdf(pdf_url, str(pdf_path))
    except Exception as e:
        click.echo(f"PDF download failed: {e}", err=True)
        sys.exit(1)

    try:
        text = extract_text(pdf_path)
        write_text(paper_dir / "content.txt", text)
    except Exception as e:
        click.echo(f"Text extraction failed: {e}", err=True)

    try:
        from archivist.services.pdf_extract import extract_figures
        figs = extract_figures(pdf_path, paper_dir / "figures")
        if figs:
            click.echo(f"Extracted {len(figs)} figure(s)")
    except Exception as e:
        click.echo(f"Figure extraction failed: {e}", err=True)

    meta = PaperMeta(
        id=generate_id(),
        title=title or arxiv_id,
        slug=slug,
        year=year,
        arxiv_id=arxiv_id,
        source_filename=f"{arxiv_id}.pdf",
    )
    write_json(paper_dir / "meta.json", meta.to_dict())
    click.echo(f"Archived: {paper_dir}")


def _arxiv_slug(title: str) -> str:
    """Generate a slug from paper title."""
    from archivist.utils import slugify
    return slugify(title)


# ── Digest commands ───────────────────────────────────────


@cli.group("digest")
def digest_group():
    """Generate daily/weekly/monthly digest reports."""
    pass


@digest_group.command("daily-prepare")
@click.option("--date", "-d", default=None, help="YYYY-MM-DD, default: today")
def cmd_digest_daily_prepare(date: str | None):
    """Output daily papers as JSON for agent analysis."""
    import json
    from datetime import datetime as dt
    from archivist.services.digest import prepare_daily
    if not date:
        date = dt.now().strftime("%Y-%m-%d")
    data = prepare_daily(date)
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


@digest_group.command("daily-write")
@click.option("--date", "-d", required=True, help="YYYY-MM-DD")
@click.option("--json", "json_path", required=True, help="Agent-produced JSON file")
def cmd_digest_daily_write(date: str, json_path: str):
    """Write daily digest from agent's analysis."""
    import json
    from archivist.services.digest import write_daily
    agent_data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    out = write_daily(date, agent_data)
    click.echo(f"Written: {out}")


@digest_group.command("weekly-prepare")
@click.option("--week", "-w", default=None, help="YYYY-Www, default: current week")
def cmd_digest_weekly_prepare(week: str | None):
    """Output weekly aggregation as JSON for agent analysis."""
    import json
    from datetime import date as dt
    from archivist.services.digest import prepare_weekly
    if not week:
        today = dt.today()
        iso = today.isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"
    data = prepare_weekly(week)
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


@digest_group.command("weekly-write")
@click.option("--week", "-w", required=True)
@click.option("--json", "json_path", required=True)
def cmd_digest_weekly_write(week: str, json_path: str):
    """Write weekly digest from agent's analysis."""
    import json
    from archivist.services.digest import write_weekly
    agent_data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    out = write_weekly(week, agent_data)
    click.echo(f"Written: {out}")


@digest_group.command("monthly-prepare")
@click.option("--month", "-m", default=None, help="YYYY-MM, default: current month")
def cmd_digest_monthly_prepare(month: str | None):
    """Output monthly aggregation as JSON for agent analysis."""
    import json
    from datetime import date as dt
    from archivist.services.digest import prepare_monthly
    if not month:
        month = dt.today().strftime("%Y-%m")
    data = prepare_monthly(month)
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


@digest_group.command("monthly-write")
@click.option("--month", "-m", required=True)
@click.option("--json", "json_path", required=True)
def cmd_digest_monthly_write(month: str, json_path: str):
    """Write monthly digest from agent's analysis."""
    import json
    from archivist.services.digest import write_monthly
    agent_data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    out = write_monthly(month, agent_data)
    click.echo(f"Written: {out}")


@digest_group.command("list")
def cmd_digest_list():
    """List all digest reports."""
    from archivist.services.digest import load_digests
    digests = load_digests()
    if not digests:
        click.echo("No digests found.")
        return
    for d in digests:
        click.echo(f"  [{d.period_type:7}] {d.id} — {d.title} ({d.paper_count} papers)")


# ── End-to-end orchestrator commands ─────────────────────────


@digest_group.command("run")
@click.option("--date", "-d", default=None,
              help="YYYY-MM-DD; default: today")
def cmd_digest_run(date: str | None):
    """Full daily digest pipeline (fetch → score → read → digest → push → deploy)."""
    from archivist.services.digest_runner import run_daily
    try:
        run_daily(date)
    except Exception as e:
        click.echo(f"daily digest failed: {e}", err=True)
        raise SystemExit(1)


@digest_group.command("run-weekly")
@click.option("--week", "-w", default=None,
              help="YYYY-Www; default: current ISO week")
def cmd_digest_run_weekly(week: str | None):
    """Full weekly digest pipeline (aggregate → digest → push → deploy)."""
    from archivist.services.digest_runner import run_weekly
    try:
        run_weekly(week)
    except Exception as e:
        click.echo(f"weekly digest failed: {e}", err=True)
        raise SystemExit(1)


@digest_group.command("run-monthly")
@click.option("--month", "-m", default=None,
              help="YYYY-MM; default: current month")
def cmd_digest_run_monthly(month: str | None):
    """Full monthly digest pipeline (aggregate → digest → push → deploy)."""
    from archivist.services.digest_runner import run_monthly
    try:
        run_monthly(month)
    except Exception as e:
        click.echo(f"monthly digest failed: {e}", err=True)
        raise SystemExit(1)


# ── Web commands ──────────────────────────────────────────


@cli.command("web")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8080, type=int, help="Port to bind to")
@click.option("--debug", is_flag=True, help="Enable debug mode")
def web_serve(host: str, port: int, debug: bool):
    """Start the local development web server."""
    from archivist.web import create_app
    app = create_app(mode="dev")
    click.echo(f"Starting Archivist web at http://{host}:{port}/")
    app.run(host=host, port=port, debug=debug)


@cli.command("build")
@click.option("--output", "-o", default="_site", type=click.Path())
def build_site(output: str):
    """Build the static website into the output directory."""
    from archivist.web.build import build_site
    build_site(Path(output))


@cli.command("deploy")
@click.option("--output", "-o", default="_site", type=click.Path())
@click.option("--host", default=None,
              help="Override deploy.host from config.yaml. Format user@host.")
@click.option("--skip-build", is_flag=True, help="Skip the build step and only rsync.")
def deploy_site(output: str, host: str | None, skip_build: bool):
    """Build the site and rsync it (plus paper figures) to the configured host."""
    import subprocess
    from archivist.config import PAPERS_DIR

    settings = get_deploy_settings()
    target_host = host or settings["host"]
    if not target_host:
        raise click.ClickException(
            "no deploy host configured (set deploy.host in config.yaml or pass --host)"
        )
    site_path = settings["remote_site_path"]
    archive_path = settings["remote_archive_path"]
    remote_papers_path = f"{archive_path}/papers"

    output_path = Path(output)
    if not skip_build:
        from archivist.web.build import build_site
        build_site(output_path)

    # 1) Static site
    click.echo(f"→ rsync {output_path}/ to {target_host}:{site_path}/")
    subprocess.run(
        ["rsync", "-avz", "--delete", f"{output_path}/", f"{target_host}:{site_path}/"],
        check=True,
    )

    # 2) Paper figures + reading.md + meta.json. nginx aliases
    #    /reading/<year>/<slug>/figures/ → <archive_path>/papers/<year>/<slug>/figures/
    #    on the server, so figures must live in the server-side archive tree
    #    in addition to the static site.
    click.echo(f"→ rsync {PAPERS_DIR}/ to {target_host}:{remote_papers_path}/ (figures + meta)")
    subprocess.run([
        "rsync", "-avz",
        "--include=*/",
        "--include=figures/***",
        "--include=document.pdf",
        "--include=meta.json",
        "--include=reading.md",
        "--exclude=*",
        f"{str(PAPERS_DIR)}/",
        f"{target_host}:{remote_papers_path}/",
    ], check=True)
    click.echo("Deploy complete.")


@cli.command("notify")
@click.option("--text", "-t", required=True, help="Plain-text message body.")
def notify_lark(text: str):
    """Send a plain-text notification to the configured Lark user.

    Used by cron wrappers to report job status. Silently no-ops when
    lark.notify_user_id is unset (minimal-install mode).
    """
    from archivist.config import get_lark_user_id
    if not get_lark_user_id():
        click.echo("(notify skipped: lark.notify_user_id not configured)")
        return
    from archivist.services.lark_push import LarkPushError, send_text_notification
    try:
        msg_id = send_text_notification(text)
        click.echo(msg_id)
    except LarkPushError as e:
        click.echo(f"lark notify failed: {e}", err=True)
        raise SystemExit(1)


# ── Search command ──────────────────────────────────────────


@cli.command()
@click.argument("query")
@click.option("--type", "doc_type", type=click.Choice(["paper", "doc", "all"]), default="all", help="Search type")
@click.option("--tag", help="Filter by tag")
def search(query: str, doc_type: str, tag: str | None):
    """Search papers and documents by keyword."""
    from archivist.config import DOCS_DIR

    results = []
    query_lower = query.lower()

    # Search papers (both read and brief)
    if doc_type in ("paper", "all"):
        from itertools import chain
        paper_metas = chain(
            PAPERS_DIR.rglob("meta.json") if PAPERS_DIR.exists() else [],
            PAPERS_BRIEF_DIR.rglob("meta.json") if PAPERS_BRIEF_DIR.exists() else [],
        )
        for meta_file in paper_metas:
            data = paper_store.read_json(meta_file)
            if tag and tag not in data.get("tags", []):
                continue
            # Search in metadata
            searchable = " ".join([
                data.get("title", ""),
                data.get("abstract", ""),
                " ".join(data.get("authors", [])),
                " ".join(data.get("tags", [])),
                data.get("one_line_summary", ""),
                data.get("notes", ""),
            ]).lower()
            if query_lower in searchable:
                results.append(("paper", data.get("slug", ""), data.get("title", "")))
                continue
            # Search in content.txt
            content_file = meta_file.parent / "content.txt"
            if content_file.exists():
                content = content_file.read_text(encoding="utf-8").lower()
                if query_lower in content:
                    results.append(("paper", data.get("slug", ""), data.get("title", "")))

    # Search docs
    if doc_type in ("doc", "all"):
        for meta_file in DOCS_DIR.rglob("meta.json"):
            data = doc_store.read_json(meta_file)
            if tag and tag not in data.get("tags", []):
                continue
            searchable = " ".join([
                data.get("title", ""),
                data.get("description", ""),
                " ".join(data.get("tags", [])),
            ]).lower()
            if query_lower in searchable:
                results.append(("doc", data.get("slug", ""), data.get("title", "")))
                continue
            content_file = meta_file.parent / "content.md"
            if content_file.exists():
                content = content_file.read_text(encoding="utf-8").lower()
                if query_lower in content:
                    results.append(("doc", data.get("slug", ""), data.get("title", "")))

    if not results:
        click.echo(f"No results for: {query}")
        return

    for dtype, slug, title in results:
        icon = "📄" if dtype == "paper" else "📝"
        click.echo(f"  {icon} [{dtype}] {slug} — {title}")
    click.echo(f"\n{len(results)} result(s)")


# ── Stats and tags ──────────────────────────────────────────


@cli.command()
def tags():
    """Show tag statistics."""
    from collections import Counter
    tag_counts: Counter[str] = Counter()

    from itertools import chain
    from archivist.config import DOCS_DIR
    paper_metas = chain(
        PAPERS_DIR.rglob("meta.json") if PAPERS_DIR.exists() else [],
        PAPERS_BRIEF_DIR.rglob("meta.json") if PAPERS_BRIEF_DIR.exists() else [],
    )
    for meta_file in paper_metas:
        data = paper_store.read_json(meta_file)
        for t in data.get("tags", []):
            tag_counts[t] += 1
    for meta_file in DOCS_DIR.rglob("meta.json"):
        data = doc_store.read_json(meta_file)
        for t in data.get("tags", []):
            tag_counts[t] += 1

    if not tag_counts:
        click.echo("No tags found.")
        return

    for tag, count in tag_counts.most_common():
        click.echo(f"  {tag}: {count}")


@cli.command()
def stats():
    """Show archive statistics."""
    from archivist.config import DOCS_DIR

    read_count = sum(1 for _ in PAPERS_DIR.rglob("meta.json")) if PAPERS_DIR.exists() else 0
    brief_count = sum(1 for _ in PAPERS_BRIEF_DIR.rglob("meta.json")) if PAPERS_BRIEF_DIR.exists() else 0
    doc_count = sum(1 for _ in DOCS_DIR.rglob("meta.json")) if DOCS_DIR.exists() else 0

    click.echo(f"Papers (精读):   {read_count}")
    click.echo(f"Papers (摘要):   {brief_count}")
    click.echo(f"Documents:       {doc_count}")
    click.echo(f"Total:           {read_count + brief_count + doc_count}")


if __name__ == "__main__":
    cli()
