"""PDF text, metadata, and figure extraction using pymupdf."""

from pathlib import Path

import pymupdf


def extract_text(pdf_path: Path) -> str:
    """Extract all text from a PDF file."""
    doc = pymupdf.open(str(pdf_path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def extract_metadata(pdf_path: Path) -> dict:
    """Extract PDF metadata (title, author, etc.)."""
    doc = pymupdf.open(str(pdf_path))
    meta = doc.metadata or {}
    doc.close()
    return {
        "title": meta.get("title", "") or "",
        "author": meta.get("author", "") or "",
        "subject": meta.get("subject", "") or "",
        "keywords": meta.get("keywords", "") or "",
    }


def extract_figures(pdf_path: Path, output_dir: Path, min_size: int = 400) -> list[str]:
    """Extract bitmap images from a PDF file, skipping small icons and duplicates.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory to save extracted images (created if needed).
        min_size: Minimum width or height in pixels to keep an image.

    Returns:
        List of saved filenames (e.g. ["fig_p3_0_640x480.png", ...]).
    """
    import hashlib

    doc = pymupdf.open(str(pdf_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    seen_hashes: set[str] = set()
    img_index = 0
    for page_num, page in enumerate(doc):
        images = page.get_images(full=True)
        for img_info in images:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue
            if not base_image:
                continue

            width = base_image["width"]
            height = base_image["height"]
            if width < min_size and height < min_size:
                continue

            # Deduplicate by content hash
            img_hash = hashlib.md5(base_image["image"]).hexdigest()
            if img_hash in seen_hashes:
                continue
            seen_hashes.add(img_hash)

            ext = base_image["ext"]
            if ext not in ("png", "jpeg", "jpg"):
                ext = "png"

            filename = f"fig_p{page_num + 1}_{img_index}_{width}x{height}.{ext}"
            (output_dir / filename).write_bytes(base_image["image"])
            saved.append(filename)
            img_index += 1

    doc.close()
    return saved
