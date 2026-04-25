"""Paper storage: import, list, show, edit, remove."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from archivist.config import PAPERS_DIR, PAPERS_BRIEF_DIR
from archivist.models import PaperMeta
from archivist.services.pdf_extract import extract_figures, extract_metadata, extract_text
from archivist.utils import generate_id, read_json, slugify, write_json, write_text


def import_paper(
    pdf_path: Path,
    title: str | None = None,
    tags: list[str] | None = None,
    category: list[str] | str | None = None,
) -> PaperMeta:
    """Import a PDF paper into the archive.

    Copies PDF, extracts text and metadata, writes meta.json and content.txt.
    Returns the created PaperMeta.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Extract PDF metadata for defaults
    pdf_meta = extract_metadata(pdf_path)

    if not title:
        title = pdf_meta["title"] or pdf_path.stem

    now = datetime.now(timezone.utc)
    year = now.year
    slug = slugify(title)

    # Ensure unique slug
    paper_dir = PAPERS_DIR / str(year) / slug
    if paper_dir.exists():
        slug = f"{slug}-{generate_id()[:6]}"
        paper_dir = PAPERS_DIR / str(year) / slug

    paper_dir.mkdir(parents=True, exist_ok=True)

    # Copy PDF
    dest_pdf = paper_dir / "document.pdf"
    shutil.copy2(pdf_path, dest_pdf)

    # Extract text
    text = extract_text(pdf_path)
    write_text(paper_dir / "content.txt", text)

    # Extract figures
    extract_figures(pdf_path, paper_dir / "figures")

    # Parse authors from PDF metadata
    authors = []
    if pdf_meta["author"]:
        authors = [a.strip() for a in pdf_meta["author"].split(",") if a.strip()]

    if category is None:
        cat_list = ["other"]
    elif isinstance(category, str):
        cat_list = [category] if category else ["other"]
    else:
        cat_list = list(category) or ["other"]

    meta = PaperMeta(
        id=generate_id(),
        title=title,
        slug=slug,
        year=year,
        authors=authors,
        source_filename=pdf_path.name,
        tags=tags or [],
        category=cat_list,
    )
    write_json(paper_dir / "meta.json", meta.to_dict())
    return meta


def _all_paper_dirs() -> list[Path]:
    """Return both papers and papers_brief directories."""
    return [d for d in [PAPERS_DIR, PAPERS_BRIEF_DIR] if d.exists()]


def list_papers(
    tag: str | None = None,
    year: int | None = None,
    status: str | None = None,
    category: str | None = None,
) -> list[PaperMeta]:
    """List all papers (both read and brief), optionally filtered."""
    papers = []
    for base_dir in _all_paper_dirs():
        for meta_file in base_dir.rglob("meta.json"):
            data = read_json(meta_file)
            paper = PaperMeta.from_dict(data)
            if tag and tag not in paper.tags:
                continue
            if year and paper.year != year:
                continue
            if status and paper.read_status != status:
                continue
            if category and category not in paper.category:
                continue
            papers.append(paper)

    papers.sort(key=lambda p: p.date_added, reverse=True)
    return papers


def get_paper(slug: str) -> PaperMeta | None:
    """Find a paper by slug (searches both papers and papers_brief)."""
    for base_dir in _all_paper_dirs():
        for meta_file in base_dir.rglob("meta.json"):
            data = read_json(meta_file)
            if data.get("slug") == slug:
                return PaperMeta.from_dict(data)
    return None


def get_paper_dir(slug: str) -> Path | None:
    """Get the directory path for a paper by slug (searches both dirs)."""
    for base_dir in _all_paper_dirs():
        for meta_file in base_dir.rglob("meta.json"):
            data = read_json(meta_file)
            if data.get("slug") == slug:
                return meta_file.parent
    return None


def update_paper_at(paper_dir: Path, **kwargs) -> PaperMeta:
    """Update meta.json at a known paper_dir (skips slug search)."""
    data = read_json(paper_dir / "meta.json")
    for key, value in kwargs.items():
        if value is not None:
            data[key] = value
    data["date_modified"] = datetime.now(timezone.utc).isoformat()
    write_json(paper_dir / "meta.json", data)
    return PaperMeta.from_dict(data)


def update_paper(slug: str, **kwargs) -> PaperMeta | None:
    """Update paper metadata fields (searches for slug first)."""
    paper_dir = get_paper_dir(slug)
    if not paper_dir:
        return None
    return update_paper_at(paper_dir, **kwargs)


def remove_paper(slug: str) -> bool:
    """Remove a paper and its directory."""
    paper_dir = get_paper_dir(slug)
    if not paper_dir:
        return False
    shutil.rmtree(paper_dir)
    return True
