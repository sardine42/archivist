"""PDF text, metadata, and figure extraction using pymupdf.

This module is the single source of truth for figure extraction. The
``scripts/extract-figures.py`` CLI and the ``paper_store`` / CLI ``paper add``
paths all funnel through :func:`extract_all_figures` so every entry point
produces the same captioned, vector-aware ``figures.json`` output.
"""

import json
import re
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


# ── Figure extraction ────────────────────────────────────────────────────────

_CAP_RE = re.compile(r"^\s*(figure|fig|table|tab)\.?\s*\d+", re.IGNORECASE)
_TABLE_RE = re.compile(r"^\s*(table|tab)\b", re.IGNORECASE)
_CAP_MAXLEN = 500


def _merge_nearby_boxes(boxes: list[pymupdf.Rect], gap: float = 20) -> list[pymupdf.Rect]:
    """Merge bounding boxes that are within ``gap`` points of each other."""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda r: (r.y0, r.x0))
    merged = [boxes[0]]
    for box in boxes[1:]:
        last = merged[-1]
        expanded = pymupdf.Rect(last.x0 - gap, last.y0 - gap, last.x1 + gap, last.y1 + gap)
        if expanded.intersects(box):
            merged[-1] = last | box  # Union
        else:
            merged.append(box)
    # Repeat merge pass until stable (handles chain merges)
    changed = True
    while changed:
        changed = False
        new_merged = []
        used = set()
        for i, a in enumerate(merged):
            if i in used:
                continue
            current = a
            for j, b in enumerate(merged):
                if j <= i or j in used:
                    continue
                expanded = pymupdf.Rect(current.x0 - gap, current.y0 - gap,
                                        current.x1 + gap, current.y1 + gap)
                if expanded.intersects(b):
                    current = current | b
                    used.add(j)
                    changed = True
            new_merged.append(current)
            used.add(i)
        merged = new_merged
    return merged


def _caption_anchors(page) -> list[dict]:
    """All caption blocks on the page as ``{rect, text, kind}``.

    Uses block-level extraction (``get_text("blocks")``) instead of a clipped
    first-line scan. A caption paragraph is normally its own block, so this
    captures the full multi-line caption without truncating words or dropping
    continuation lines.
    """
    anchors = []
    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        t = " ".join(text.split())  # collapse newlines/runs of whitespace
        if not _CAP_RE.match(t):
            continue
        if len(t) > _CAP_MAXLEN:  # guard against a caption glued to body text
            t = t[:_CAP_MAXLEN].rstrip() + "…"
        kind = "table" if _TABLE_RE.match(t) else "figure"
        anchors.append({"rect": pymupdf.Rect(x0, y0, x1, y1), "text": t, "kind": kind})
    return anchors


def _match_caption(fig: pymupdf.Rect, anchors: list[dict], max_gap: float = 130) -> dict:
    """Pick the caption block nearest a figure box, preferring below then above.

    Requires horizontal overlap so a caption sitting in the *other* column of a
    two-column layout is never stolen. Returns {} when nothing matches; the same
    anchor may legitimately serve two side-by-side panels of one logical figure.
    A wrong caption is worse than none (it misplaces the figure), so ``max_gap``
    stays conservative.
    """
    best, best_key = {}, None
    for a in anchors:
        r = a["rect"]
        if min(fig.x1, r.x1) - max(fig.x0, r.x0) <= 0:  # no horizontal overlap
            continue
        if r.y0 >= fig.y1 - 8:            # caption below the figure
            gap, where = r.y0 - fig.y1, 0
        elif r.y1 <= fig.y0 + 8:          # caption above the figure
            gap, where = fig.y0 - r.y1, 1
        else:
            continue
        if gap > max_gap:
            continue
        key = (where, gap)                # below (0) beats above (1), then nearest
        if best_key is None or key < best_key:
            best_key, best = key, a
    return best


def extract_all_figures(pdf_path: Path, output_dir: Path,
                        dpi: int = 200, min_size: int = 200) -> list[dict]:
    """Extract all figures (vector + bitmap) from a PDF.

    For each page: collect vector-drawing clusters and bitmap placement rects,
    merge nearby boxes into figure regions, render each region to PNG at ``dpi``,
    and attach the nearest caption block.

    Returns list of ``{filename, page, kind, caption, bbox}`` and writes
    ``figures.json`` to ``output_dir`` when any figure is found.
    """
    doc = pymupdf.open(str(pdf_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    figures = []
    fig_index = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_rect = page.rect
        anchors = _caption_anchors(page)
        all_boxes = []

        # 1. Vector graphics via cluster_drawings
        try:
            for rect in page.cluster_drawings():
                r = pymupdf.Rect(rect)
                # Skip tiny drawings and full-page borders
                if r.width < min_size / 3 or r.height < min_size / 3:
                    continue
                if r.width > page_rect.width * 0.95 and r.height > page_rect.height * 0.95:
                    continue
                all_boxes.append(r)
        except Exception:
            pass

        # 2. Bitmap images via get_images
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                for r in page.get_image_rects(xref):
                    r = pymupdf.Rect(r)
                    if r.width < min_size / 3 or r.height < min_size / 3:
                        continue
                    all_boxes.append(r)
            except Exception:
                continue

        if not all_boxes:
            continue

        # 3. Merge nearby boxes into figure regions
        merged = _merge_nearby_boxes(all_boxes)

        # 4. Extract each merged region
        for bbox in merged:
            # Skip if too small after merge
            if bbox.width < min_size / 2 and bbox.height < min_size / 2:
                continue
            # Skip if it's basically the whole page
            if bbox.width > page_rect.width * 0.9 and bbox.height > page_rect.height * 0.85:
                continue

            # Add small padding
            padded = pymupdf.Rect(
                max(0, bbox.x0 - 5),
                max(0, bbox.y0 - 5),
                min(page_rect.x1, bbox.x1 + 5),
                min(page_rect.y1, bbox.y1 + 5),
            )

            # Match the nearest caption block (full multi-line text + kind)
            match = _match_caption(padded, anchors)
            caption = match.get("text", "")
            kind = match.get("kind", "figure")

            # Render to image
            fig_index += 1
            filename = f"fig_{fig_index:02d}.png"
            mat = pymupdf.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, clip=padded)
            pix.save(str(output_dir / filename))

            figures.append({
                "filename": filename,
                "page": page_num + 1,
                "kind": kind,
                "caption": caption,
                "bbox": [round(padded.x0, 1), round(padded.y0, 1),
                         round(padded.x1, 1), round(padded.y1, 1)],
            })

    doc.close()

    # Save metadata
    if figures:
        (output_dir / "figures.json").write_text(
            json.dumps(figures, indent=2, ensure_ascii=False) + "\n"
        )

    return figures


def extract_figures(pdf_path: Path, output_dir: Path) -> list[str]:
    """Backwards-compatible wrapper: extract figures, return filenames.

    Kept for ``paper_store.save_paper`` and the CLI ``paper add`` path, which
    only need the saved filenames. Delegates to :func:`extract_all_figures` so
    those paths get the same vector-aware, captioned output as the deep-read
    pipeline instead of the old bitmap-only extraction.
    """
    return [f["filename"] for f in extract_all_figures(pdf_path, output_dir)]
