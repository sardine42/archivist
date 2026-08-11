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
    """Pick the caption block nearest a figure box, on the side its kind implies.

    Requires horizontal overlap so a caption sitting in the *other* column of a
    two-column layout is never stolen. Returns {} when nothing matches; the same
    anchor may legitimately serve two side-by-side panels of one logical figure.
    A wrong caption is worse than none (it misplaces the figure), so ``max_gap``
    stays conservative.

    Typesetting convention puts figure captions *below* the artwork and table
    captions *above* the table. Preferring "below" unconditionally therefore
    mislabelled every table screenshot that had anything captioned beneath it —
    and what sits beneath a table is usually the *next* numbered item, so the
    label could be assigned to the next numbered item. Scoring each anchor on the side its own kind implies fixes that
    without touching figure behaviour.
    """
    best, best_key = {}, None
    for a in anchors:
        r = a["rect"]
        if min(fig.x1, r.x1) - max(fig.x0, r.x0) <= 0:  # no horizontal overlap
            continue
        if r.y0 >= fig.y1 - 8:            # anchor sits below the image
            gap, natural = r.y0 - fig.y1, a["kind"] == "figure"
        elif r.y1 <= fig.y0 + 8:          # anchor sits above the image
            gap, natural = fig.y0 - r.y1, a["kind"] == "table"
        else:
            continue
        if gap > max_gap:
            continue
        # conventional side (0) beats the other side (1), then nearest wins
        key = (0 if natural else 1, gap)
        if best_key is None or key < best_key:
            best_key, best = key, a
    return best


def _split_box_at_captions(box: pymupdf.Rect, anchors: list[dict],
                           page_rect: pymupdf.Rect | None = None,
                           min_h: float = 40) -> list[pymupdf.Rect]:
    """Cut a box wherever a caption sits inside it.

    ``cluster_drawings()`` sometimes returns one cluster for a whole column,
    chaining a table's rules into the figure below it. Captions never sit
    *inside* one figure's artwork, so any caption strictly within the box marks
    a boundary between two items. Panel labels like "(a)" don't match _CAP_RE,
    so multi-panel figures are unaffected.
    """
    # A page-sized cluster is cluster_drawings() failing, not a real region —
    # the caller already discards those. Splitting it just turns one discarded
    # blob into several half-page crops of body text, each stealing a caption.
    if page_rect is not None and (box.width > page_rect.width * 0.9
                                  and box.height > page_rect.height * 0.85):
        return [box]
    cuts = []
    for a in anchors:
        r = a["rect"]
        if r.y0 <= box.y0 or r.y1 >= box.y1:
            continue  # not strictly inside
        if min(box.x1, r.x1) - max(box.x0, r.x0) <= 0:
            continue  # other column
        cuts.append((r.y0, r.y1))
    if not cuts:
        return [box]
    cuts.sort()
    parts, top = [], box.y0
    for lo, hi in cuts:
        if lo - top >= min_h:
            parts.append(pymupdf.Rect(box.x0, top, box.x1, lo))
        top = max(top, hi)
    if box.y1 - top >= min_h:
        parts.append(pymupdf.Rect(box.x0, top, box.x1, box.y1))
    return parts or [box]


def _merge_by_caption_owner(boxes: list[pymupdf.Rect], anchors: list[dict],
                            gap: float = 20,
                            page_rect: pymupdf.Rect | None = None) -> list[pymupdf.Rect]:
    """Merge boxes, but only ones that claim the same caption.

    Proximity alone over-merges: a table body and the figure below it are
    typically closer to each other than 20pt, yet nothing sits between them —
    a table's caption is above it and a figure's is below, so consecutive items
    leave no separator in the gap. Distance-only merging can therefore combine several numbered items.

    Letting every box claim its own caption first, and merging only within a
    claim, keeps multi-panel figures together (all panels match the one caption
    beneath them) while never fusing two numbered items. Boxes that match no
    caption fall back to plain proximity merging among themselves.
    """
    split: list[pymupdf.Rect] = []
    for box in boxes:
        split.extend(_split_box_at_captions(box, anchors, page_rect))
    groups: dict[int, list[pymupdf.Rect]] = {}
    for box in split:
        match = _match_caption(box, anchors)
        # anchors hold no natural id; position in the list is a stable key
        key = next((i for i, a in enumerate(anchors) if a is match), -1)
        groups.setdefault(key, []).append(box)
    out = []
    for key, group in groups.items():
        out.extend(_merge_nearby_boxes(group, gap=gap))
    # Grouping scrambles page order; fig_NN must still follow reading order.
    return sorted(out, key=lambda r: (r.y0, r.x0))


def _covers_page(r: pymupdf.Rect, page_rect: pymupdf.Rect) -> bool:
    """Whether ``r`` spans essentially the whole page (border or background)."""
    return r.width > page_rect.width * 0.95 and r.height > page_rect.height * 0.95


def _hugs_figure_caption(r: pymupdf.Rect, anchors: list[dict],
                         gap: float = 26) -> bool:
    """Whether a ``Figure N:`` caption sits immediately below ``r``.

    Rescues wide-flat artwork that the min-side guard would drop, including
    multi-panel schematics with a short vertical span.

    Restricted to *figure* captions on purpose. Table captions sit above their
    table, and a table's rules cluster into separate thin bands, so allowing the
    table side rescued header strips and single-row fragments rather than tables.
    """
    for a in anchors:
        if a["kind"] != "figure":
            continue
        ar = a["rect"]
        if min(r.x1, ar.x1) - max(r.x0, ar.x0) <= 0:  # no horizontal overlap
            continue
        if 0 <= ar.y0 - r.y1 <= gap:
            return True
    return False


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
            # Some templates paint a page-sized background rect on every page.
            # It bridges every drawing into one page-wide cluster, which the
            # full-page guard below then discards -- losing all figures. Drop
            # such rects first; when there are none the call is unchanged.
            drawings = page.get_drawings()
            content = [d for d in drawings
                       if not _covers_page(pymupdf.Rect(d["rect"]), page_rect)]
            clusters = (page.cluster_drawings() if len(content) == len(drawings)
                        else page.cluster_drawings(drawings=content))
            for rect in clusters:
                r = pymupdf.Rect(rect)
                if _covers_page(r, page_rect):  # full-page border
                    continue
                # Skip tiny drawings, unless a figure caption hugs the box --
                # wide-flat panels are real figures the size guard would lose.
                if r.width < min_size / 3 or r.height < min_size / 3:
                    if (max(r.width, r.height) < min_size / 3
                            or min(r.width, r.height) < 40
                            or not _hugs_figure_caption(r, anchors)):
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

        # 3. Merge nearby boxes into figure regions, one region per caption
        merged = _merge_by_caption_owner(all_boxes, anchors, page_rect=page_rect)

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
