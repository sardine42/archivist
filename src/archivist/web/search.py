"""Build the paper-only Pagefind index used by static and preview sites.

Index explicit public fields rather than crawling rendered pages: detail pages
also contain private rating reasons and links to unrelated papers.
"""

import asyncio
import gzip
import json
from collections.abc import Iterable, Mapping
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from pagefind.index import IndexConfig, PagefindIndex

from archivist.config import PAPERS_DIR
from archivist.models import PaperMeta
from archivist.services.paper_store import list_papers


def _search_document(paper: PaperMeta, reading_html: str) -> str:
    """Create HTML containing only the public paper search corpus."""
    public_fields = (
        paper.abstract,
        paper.one_line_summary,
        paper.one_line_summary_en,
        " ".join(paper.authors),
        " ".join(paper.affiliations),
        " ".join(paper.tags),
        paper.arxiv_id or "",
    )
    metadata = "".join(f"<p>{escape(value)}</p>" for value in public_fields if value)
    return (
        '<html lang="zh"><head>'
        f'<meta data-pagefind-meta="slug[content]" content="{escape(paper.slug, quote=True)}">'
        '</head><body><main data-pagefind-body>'
        f'<h1 data-pagefind-meta="title" data-pagefind-weight="10">{escape(paper.title)}</h1>'
        '<p data-pagefind-meta="model_name" data-pagefind-weight="10">'
        f'{escape(paper.model_name)}</p>'
        f'{metadata}<article>{reading_html}</article>'
        '</main></body></html>'
    )


async def _write_index(output_dir: Path, documents: list[tuple[PaperMeta, str]]) -> None:
    config = IndexConfig(output_path=str(output_dir), force_language="zh")
    # The Python wrapper sleeps between individual service responses. Adding
    # hundreds of files individually would impose that delay hundreds of times.
    # One directory request indexes the same explicit corpus in a single batch.
    with TemporaryDirectory(prefix="archivist-search-source-") as directory:
        source_dir = Path(directory)
        for paper, content in documents:
            source_file = source_dir / "reading" / str(paper.year) / paper.slug / "index.html"
            source_file.parent.mkdir(parents=True, exist_ok=True)
            source_file.write_text(_search_document(paper, content), encoding="utf-8")
        async with PagefindIndex(config=config) as index:
            if documents:
                await index.add_directory(str(source_dir))


def _write_paper_manifest(output_dir: Path, expected_count: int) -> None:
    """Expose IDs without forcing the browser to download every result body.

    Pagefind's search result ID is its fragment filename. Fragments contain a
    gzip-compressed JSON document prefixed by the Pagefind signature; use the slug
    metadata to provide a compact lookup for ranking and filtering the cards.
    Keep the format check explicit so an incompatible Pagefind update fails
    the build instead of silently dropping search results.
    """
    manifest = {}
    for fragment in sorted((output_dir / "fragment").glob("*.pf_fragment")):
        data = fragment.read_bytes()
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        if not data.startswith(b"pagefind_dcd"):
            raise RuntimeError("Unsupported Pagefind fragment format; cannot map paper results")
        record = json.loads(data[12:])
        slug = record.get("meta", {}).get("slug")
        if not slug:
            raise RuntimeError("Pagefind paper fragment is missing its slug metadata")
        manifest[fragment.stem] = slug
    if len(manifest) != expected_count:
        raise RuntimeError(
            f"Pagefind indexed {len(manifest)} papers; expected {expected_count}"
        )
    (output_dir / "papers.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def build_paper_search(
    output_dir: Path,
    papers: Iterable[PaperMeta] | None = None,
    rendered_content: Mapping[str, str] | None = None,
) -> int:
    """Write a self-contained search bundle and return its published paper count."""
    from archivist.web.routes.reading import (
        _build_paper_lookups,
        _render_markdown,
        _resolve_wiki_links,
    )

    paper_list = [
        paper for paper in (papers if papers is not None else list_papers())
        if (PAPERS_DIR / str(paper.year) / paper.slug / "reading.md").is_file()
    ]
    by_arxiv, by_slug = _build_paper_lookups([{"meta": paper} for paper in paper_list])
    documents = []
    for paper in paper_list:
        reading_file = PAPERS_DIR / str(paper.year) / paper.slug / "reading.md"
        if not reading_file.is_file():
            continue
        if rendered_content is not None and paper.slug in rendered_content:
            content = rendered_content[paper.slug]
        else:
            content = _render_markdown(reading_file.read_text(encoding="utf-8"))
            content = _resolve_wiki_links(content, by_arxiv, by_slug)
        documents.append((paper, content))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(_write_index(output_dir, documents))
    _write_paper_manifest(output_dir, len(documents))
    return len(documents)


_preview_lock = Lock()
_preview_signature: tuple | None = None
_preview_files: dict[str, bytes] = {}
_previous_preview_files: dict[str, bytes] = {}


def _archive_signature() -> tuple:
    """Detect additions, deletions and edits without parsing all paper metadata."""
    files = []
    for meta_file in sorted(PAPERS_DIR.glob("*/*/meta.json")):
        reading_file = meta_file.with_name("reading.md")
        if not reading_file.is_file():
            continue
        for path in (meta_file, reading_file):
            stat = path.stat()
            files.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(files)


def preview_search_asset(filename: str) -> bytes | None:
    """Return a cached asset, rebuilding the local preview on archive changes."""
    global _preview_signature, _preview_files, _previous_preview_files

    with _preview_lock:
        signature = _archive_signature()
        if signature != _preview_signature:
            # Writing files avoids the Python API's documented get_files pipe
            # deadlock on large indexes. The temporary bundle is then cached.
            with TemporaryDirectory(prefix="archivist-paper-search-") as directory:
                output_dir = Path(directory)
                build_paper_search(output_dir)
                files = {
                    path.relative_to(output_dir).as_posix(): path.read_bytes()
                    for path in output_dir.rglob("*") if path.is_file()
                }
            _previous_preview_files = _preview_files
            _preview_files = files
            _preview_signature = signature
        # Keep the last bundle's hashed chunks available to an already-open
        # search session while a changed archive is being previewed.
        return _preview_files.get(filename, _previous_preview_files.get(filename))
