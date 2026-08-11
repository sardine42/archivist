"""Document storage: add, list, show, remove."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from archivist.config import DOCS_DIR
from archivist.models import DocMeta
from archivist.utils import generate_id, read_text, slugify, write_text


def add_doc(
    file_path: Path | None = None,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    category: str = "",
    description: str = "",
) -> DocMeta:
    """Archive a document (from file or content string).

    Either file_path or content must be provided.
    """
    if file_path:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        doc_content = read_text(file_path)
        if not title:
            title = file_path.stem
    elif content:
        doc_content = content
        if not title:
            title = "untitled"
    else:
        raise ValueError("Either file_path or content must be provided")

    slug = slugify(title)
    doc_dir = DOCS_DIR / slug
    if doc_dir.exists():
        slug = f"{slug}-{generate_id()[:6]}"
        doc_dir = DOCS_DIR / slug

    doc_dir.mkdir(parents=True, exist_ok=True)

    meta = DocMeta(
        id=slug,
        title=title,
        slug=slug,
        description=description,
        tags=tags or [],
        category=category or "internal-doc",
    )
    # Write as reading.md + YAML frontmatter — the canonical doc layout the
    # website, CLI, and MCP all read (see _parse_reading / web _load_docs).
    frontmatter = {
        "title": meta.title,
        "type": meta.category,
        "date": meta.date_created[:10],
        "summary": meta.description,
        "tags": meta.tags,
    }
    fm = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    write_text(doc_dir / "reading.md", f"---\n{fm}\n---\n\n{doc_content}\n")
    return meta


def _parse_reading(md_file: Path) -> tuple[DocMeta, str]:
    """Parse a ``docs/<slug>/reading.md`` into ``(DocMeta, body)``.

    Docs are written by the ``$read-doc`` skill as ``reading.md`` with YAML
    frontmatter (``title / source / type / date / team / summary``) — there is
    no ``meta.json``. We synthesize a ``DocMeta`` from the frontmatter so the
    CLI and MCP doc tools see exactly the docs the website does (which reads
    the same layout via ``web/build.py:_load_docs``).
    """
    raw = read_text(md_file)
    fm: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2]

    slug = md_file.parent.name
    date = str(fm.get("date", "") or "")
    meta = DocMeta(
        id=slug,
        title=str(fm.get("title", "") or slug),
        slug=slug,
        description=str(fm.get("summary", "") or ""),
        tags=fm.get("tags") or [],
        category=str(fm.get("type", "") or ""),
        generated_by=str(fm.get("generated_by", "") or ""),
        date_created=date,
        date_modified=date,
    )
    return meta, body.strip()


def list_docs(
    tag: str | None = None,
    category: str | None = None,
) -> list[DocMeta]:
    """List all documents, optionally filtered."""
    docs = []
    if not DOCS_DIR.exists():
        return docs

    for md_file in DOCS_DIR.glob("*/reading.md"):
        doc, _ = _parse_reading(md_file)
        if tag and tag not in doc.tags:
            continue
        if category and doc.category != category:
            continue
        docs.append(doc)

    docs.sort(key=lambda d: d.date_created, reverse=True)
    return docs


def get_doc(slug: str) -> tuple[DocMeta, str] | None:
    """Get a document's metadata and content (reading.md body) by slug."""
    md_file = DOCS_DIR / slug / "reading.md"
    if not md_file.exists():
        return None
    return _parse_reading(md_file)


def remove_doc(slug: str) -> bool:
    """Remove a document and its directory."""
    doc_dir = DOCS_DIR / slug
    if not doc_dir.exists():
        return False
    shutil.rmtree(doc_dir)
    return True
