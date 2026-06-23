#!/usr/bin/env python3
"""Extract figures (bitmap + vector) from a PDF with captions.

Thin CLI over ``archivist.services.pdf_extract.extract_all_figures`` (the single
source of truth). Uses PyMuPDF's cluster_drawings() for vector graphics and
get_images() for bitmaps, merges nearby regions, renders each region, and
attaches the nearest caption block.

Usage:
    python3 scripts/extract-figures.py <pdf_path> <output_dir> [--dpi 200] [--min-size 200]

Output:
    - PNG files in output_dir: fig_01.png, fig_02.png, ...
    - figures.json: [{filename, page, kind, caption, bbox}, ...]
"""

import sys
from pathlib import Path

from archivist.services.pdf_extract import extract_all_figures


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
        tag = " [table]" if f.get("kind") == "table" else ""
        print(f"  p{f['page']}: {f['filename']}{tag}{cap}")


if __name__ == "__main__":
    main()
