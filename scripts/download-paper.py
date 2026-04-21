#!/usr/bin/env python3
"""下载 Arxiv 论文或导入本地 PDF 到论文库。

用法:
  python3 scripts/download-paper.py <arxiv_id> [--title "标题"]
  python3 scripts/download-paper.py --local <pdf_path> [--title "标题"]

输出论文目录路径到 stdout。
"""

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from archivist.config import PAPERS_BRIEF_DIR, PAPERS_DIR, ensure_archive_dirs
from archivist.models import PaperMeta
from archivist.utils import generate_id, slugify, write_json


def _inherit_brief_score(meta: PaperMeta, arxiv_id: str) -> None:
    """If a brief exists for this arxiv_id, copy its score and score_reason
    into the new PaperMeta so the deep-read flow preserves filter scoring."""
    if not arxiv_id:
        return
    clean = arxiv_id.rsplit("v", 1)[0] if "v" in arxiv_id and arxiv_id.rsplit("v", 1)[-1].isdigit() else arxiv_id
    brief_slug = clean.replace(".", "")
    for year_dir in PAPERS_BRIEF_DIR.iterdir() if PAPERS_BRIEF_DIR.exists() else []:
        brief_path = year_dir / brief_slug / "meta.json"
        if brief_path.exists():
            try:
                import json
                brief = json.loads(brief_path.read_text())
                meta.score = float(brief.get("score", 0.0))
                meta.score_reason = brief.get("score_reason", "")
            except (OSError, ValueError):
                pass
            return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("arxiv_id", nargs="?")
    parser.add_argument("--local", type=Path)
    parser.add_argument("--search", "-s", help="按论文标题搜索 Arxiv 并下载最佳匹配")
    parser.add_argument("--title", "-t")
    args = parser.parse_args()

    ensure_archive_dirs()
    now = datetime.now(timezone.utc)
    year = now.year

    if args.search:
        from archivist.services.arxiv_fetch import search_by_title, download_pdf
        results = search_by_title(args.search)
        if not results:
            print(f"未找到匹配论文: {args.search}", file=sys.stderr)
            sys.exit(1)
        paper = results[0]
        print(f"找到论文: {paper.title} [{paper.arxiv_id}]", file=sys.stderr)
        title = args.title or paper.title
        arxiv_id = paper.arxiv_id
        slug = slugify(title)
        paper_dir = PAPERS_DIR / str(year) / slug
        if paper_dir.exists():
            slug = f"{slug}-{arxiv_id.replace('/', '-')}"
            paper_dir = PAPERS_DIR / str(year) / slug
        paper_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = paper_dir / "document.pdf"
        print(f"Downloading {arxiv_id}...", file=sys.stderr)
        download_pdf(f"https://arxiv.org/pdf/{arxiv_id}", str(pdf_path))
        meta = PaperMeta(id=generate_id(), title=title, slug=slug, year=year,
                         arxiv_id=arxiv_id, source_filename=f"{arxiv_id}.pdf",
                         published_date=paper.published[:10] if paper.published else "")
        _inherit_brief_score(meta, arxiv_id)
    elif args.local:
        title = args.title or args.local.stem
        slug = slugify(title)
        paper_dir = PAPERS_DIR / str(year) / slug
        if paper_dir.exists():
            slug = f"{slug}-{generate_id()[:6]}"
            paper_dir = PAPERS_DIR / str(year) / slug
        paper_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.local, paper_dir / "document.pdf")
        meta = PaperMeta(id=generate_id(), title=title, slug=slug, year=year,
                         source_filename=args.local.name)
    elif args.arxiv_id:
        from archivist.services.arxiv_fetch import download_pdf, fetch_by_id
        title = args.title or args.arxiv_id
        slug = slugify(title)
        paper_dir = PAPERS_DIR / str(year) / slug
        if paper_dir.exists():
            slug = f"{slug}-{args.arxiv_id.replace('/', '-')}"
            paper_dir = PAPERS_DIR / str(year) / slug
        paper_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = paper_dir / "document.pdf"
        print(f"Downloading {args.arxiv_id}...", file=sys.stderr)
        download_pdf(f"https://arxiv.org/pdf/{args.arxiv_id}", str(pdf_path))
        published_date = ""
        try:
            raw = fetch_by_id(args.arxiv_id)
            if raw and raw.published:
                published_date = raw.published[:10]
        except Exception as e:
            print(f"warn: fetch_by_id({args.arxiv_id}) failed: {e}", file=sys.stderr)
        meta = PaperMeta(id=generate_id(), title=title, slug=slug, year=year,
                         arxiv_id=args.arxiv_id, source_filename=f"{args.arxiv_id}.pdf",
                         published_date=published_date)
        _inherit_brief_score(meta, args.arxiv_id)
    else:
        parser.error("需要 arxiv_id、--local <pdf> 或 --search <标题>")

    write_json(paper_dir / "meta.json", meta.to_dict())
    print(paper_dir)


if __name__ == "__main__":
    main()
