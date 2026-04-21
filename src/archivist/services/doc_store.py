"""Document storage: add, list, show, remove."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from archivist.config import DOCS_DIR
from archivist.models import DocMeta
from archivist.utils import generate_id, read_json, read_text, slugify, write_json, write_text


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

    write_text(doc_dir / "content.md", doc_content)

    meta = DocMeta(
        id=generate_id(),
        title=title,
        slug=slug,
        description=description,
        tags=tags or [],
        category=category,
    )
    write_json(doc_dir / "meta.json", meta.to_dict())
    return meta


def list_docs(
    tag: str | None = None,
    category: str | None = None,
) -> list[DocMeta]:
    """List all documents, optionally filtered."""
    docs = []
    if not DOCS_DIR.exists():
        return docs

    for meta_file in DOCS_DIR.rglob("meta.json"):
        data = read_json(meta_file)
        doc = DocMeta.from_dict(data)
        if tag and tag not in doc.tags:
            continue
        if category and doc.category != category:
            continue
        docs.append(doc)

    docs.sort(key=lambda d: d.date_created, reverse=True)
    return docs


def get_doc(slug: str) -> tuple[DocMeta, str] | None:
    """Get a document's metadata and content by slug."""
    doc_dir = DOCS_DIR / slug
    meta_file = doc_dir / "meta.json"
    content_file = doc_dir / "content.md"

    if not meta_file.exists():
        return None

    meta = DocMeta.from_dict(read_json(meta_file))
    content = read_text(content_file) if content_file.exists() else ""
    return meta, content


def remove_doc(slug: str) -> bool:
    """Remove a document and its directory."""
    doc_dir = DOCS_DIR / slug
    if not doc_dir.exists():
        return False
    shutil.rmtree(doc_dir)
    return True
