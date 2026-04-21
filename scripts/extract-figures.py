#!/usr/bin/env python3
"""Extract figures (bitmap + vector) from a PDF with captions.

Uses PyMuPDF's cluster_drawings() for vector graphics and get_images() for
bitmaps, merges nearby regions, and extracts captions from surrounding text.

Usage:
    python3 scripts/extract-figures.py <pdf_path> <output_dir> [--dpi 200] [--min-size 200]

Output:
    - PNG files in output_dir: fig_01.png, fig_02.png, ...
    - figures.json: [{filename, page, caption, bbox}, ...]
"""

import json
import re
import sys
from pathlib import Path

import pymupdf


def _merge_nearby_boxes(boxes: list[pymupdf.Rect], gap: float = 20) -> list[pymupdf.Rect]:
    """Merge bounding boxes that are within `gap` points of each other."""
    if not boxes:
        return []
    # Sort by y0, then x0
    boxes = sorted(boxes, key=lambda r: (r.y0, r.x0))
    merged = [boxes[0]]
    for box in boxes[1:]:
        last = merged[-1]
        # Check if boxes overlap or are close enough
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


def _find_caption(page, bbox: pymupdf.Rect, direction: str = "below") -> str:
    """Find figure/table caption text near a bounding box.

    Searches below (or above) the figure for text starting with
    'Figure', 'Fig.', 'Table', etc.
    """
    page_rect = page.rect
    if direction == "below":
        search_rect = pymupdf.Rect(
            bbox.x0 - 10, bbox.y1, bbox.x1 + 10, min(bbox.y1 + 60, page_rect.y1)
        )
    else:
        search_rect = pymupdf.Rect(
            bbox.x0 - 10, max(bbox.y0 - 60, 0), bbox.x1 + 10, bbox.y0
        )

    text = page.get_text("text", clip=search_rect).strip()
    # Look for caption pattern
    for line in text.split("\n"):
        line = line.strip()
        if re.match(r"^(Figure|Fig\.|Table|Tab\.)\s*\d+", line, re.IGNORECASE):
            return line
    return ""


def extract_all_figures(pdf_path: Path, output_dir: Path,
                        dpi: int = 200, min_size: int = 200) -> list[dict]:
    """Extract all figures (vector + bitmap) from a PDF.

    Returns list of {filename, page, caption, bbox}.
    """
    doc = pymupdf.open(str(pdf_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    figures = []
    fig_index = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_rect = page.rect
        all_boxes = []

        # 1. Vector graphics via cluster_drawings
        try:
            drawing_rects = page.cluster_drawings()
            for rect in drawing_rects:
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
                img_rects = page.get_image_rects(xref)
                for r in img_rects:
                    r = pymupdf.Rect(r)
                    if r.width < min_size / 3 or r.height < min_size / 3:
                        continue
                    all_boxes.append(r)
            except Exception:
                continue

        if not all_boxes:
            continue

        # 3. Merge nearby boxes
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

            # Find caption
            caption = _find_caption(page, padded, "below")
            if not caption:
                caption = _find_caption(page, padded, "above")

            # Render to image
            fig_index += 1
            filename = f"fig_{fig_index:02d}.png"
            mat = pymupdf.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, clip=padded)
            pix.save(str(output_dir / filename))

            figures.append({
                "filename": filename,
                "page": page_num + 1,
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


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 extract-figures.py <pdf_path> <output_dir> [--dpi N] [--min-size N]")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    dpi = 200
    min_size = 200

    for i, arg in enumerate(sys.argv):
        if arg == "--dpi" and i + 1 < len(sys.argv):
            dpi = int(sys.argv[i + 1])
        if arg == "--min-size" and i + 1 < len(sys.argv):
            min_size = int(sys.argv[i + 1])

    figures = extract_all_figures(pdf_path, output_dir, dpi=dpi, min_size=min_size)
    print(f"Extracted {len(figures)} figures to {output_dir}/")
    for f in figures:
        cap = f" — {f['caption']}" if f['caption'] else ""
        print(f"  p{f['page']}: {f['filename']}{cap}")


if __name__ == "__main__":
    main()
