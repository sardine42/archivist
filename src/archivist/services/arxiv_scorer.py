"""Paper scoring and filtering logic for Arxiv papers.

Three-stage filtering:
1. Pre-filter (code): whitelist keywords + blocked affiliations + deduplication
2. LLM scoring (Claude): evaluate relevance based on abstract — done by digest_runner
3. Deep-read selection: top_k_deep_read papers with score >= deep_read_threshold
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from archivist.config import load_config, PAPERS_BRIEF_DIR, PAPERS_DIR
from archivist.services.arxiv_fetch import RawPaper


def _clean_arxiv_id(aid: str) -> str:
    """Strip version suffix from arxiv id (1234.56789v2 -> 1234.56789)."""
    if not aid:
        return ""
    if "v" in aid:
        parts = aid.rsplit("v", 1)
        if parts[-1].isdigit():
            return parts[0]
    return aid


def _arxiv_version(aid: str) -> int:
    """Extract version number from arxiv id (default 1 if no version)."""
    if not aid or "v" not in aid:
        return 1
    parts = aid.rsplit("v", 1)
    if parts[-1].isdigit():
        return int(parts[-1])
    return 1


def _coerce_category(value) -> list[str]:
    """Coerce a category value (str | list | None) to a non-empty list[str]."""
    if value is None:
        return ["other"]
    if isinstance(value, str):
        return [value] if value else ["other"]
    out = [c for c in value if c]
    return out or ["other"]


def build_existing_index() -> dict[str, dict]:
    """Build {clean_arxiv_id: {arxiv_id, version, paper_dir, meta}} from archive/papers/."""
    index = {}
    if not PAPERS_DIR.exists():
        return index
    for paper_dir in PAPERS_DIR.glob("*/*"):
        meta_file = paper_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        aid = m.get("arxiv_id", "")
        if not aid:
            continue
        clean = _clean_arxiv_id(aid)
        index[clean] = {
            "arxiv_id": aid,
            "version": _arxiv_version(aid),
            "paper_dir": str(paper_dir),
            "slug": m.get("slug", ""),
            "year": m.get("year", 2026),
            "meta": m,
        }
    return index


@dataclass
class CandidatePaper:
    """A paper that passed pre-filtering, awaiting LLM scoring."""
    paper: RawPaper
    llm_score: float = 0.0        # 1-10, filled by LLM
    llm_reason: str = ""          # LLM's scoring rationale
    category: list[str] = field(default_factory=list)  # 子集 generative-rec / discriminative-rec / llm / other，filled by LLM
    is_blocked: bool = False
    is_existing: bool = False     # True if same version already in archive
    is_update: bool = False       # True if newer version of an existing paper
    previous_arxiv_id: str = ""   # The existing version (if is_update or is_existing)
    existing_paper_dir: str = ""  # Directory of existing version (if is_update or is_existing)
    existing_meta: dict = field(default_factory=dict)  # Existing meta.json (if is_existing)


def pre_filter(papers: list[RawPaper]) -> list[CandidatePaper]:
    """Pre-filter papers using keyword whitelist, blocked affiliations, and dedup.

    This is the code-only stage. Returns candidates for LLM scoring.

    Deduplication:
    - Skip if same arxiv version already in archive (is_duplicate)
    - Mark for update if newer version exists (is_update=True with previous_arxiv_id)
    """
    config = load_config()
    scoring = config.get("scoring", {})

    required_keywords = [k.lower() for k in scoring.get("required_keywords", [])]
    blocked_affs = [a.lower() for a in scoring.get("blocked_affiliations", [])]

    existing_index = build_existing_index()

    candidates = []
    for paper in papers:
        text_lower = f"{paper.title} {paper.abstract}".lower()

        # Whitelist gate
        if required_keywords:
            if not any(kw in text_lower for kw in required_keywords):
                continue

        # Blocked affiliation check
        all_affs = " ".join(paper.affiliations).lower()
        is_blocked = False
        if blocked_affs and all_affs:
            if all(
                any(blocked in aff_part for blocked in blocked_affs)
                for aff_part in all_affs.split(",")
                if aff_part.strip()
            ):
                is_blocked = True

        if is_blocked:
            continue

        # Deduplication: detect existing/update status, but always include
        clean_id = _clean_arxiv_id(paper.arxiv_id)
        new_version = _arxiv_version(paper.arxiv_id)
        existing = existing_index.get(clean_id)

        candidate = CandidatePaper(paper=paper)
        if existing:
            if new_version <= existing["version"]:
                # Same or older version: existing, will skip deep read
                candidate.is_existing = True
                candidate.previous_arxiv_id = existing["arxiv_id"]
                candidate.existing_paper_dir = existing["paper_dir"]
                candidate.existing_meta = existing["meta"]
            else:
                # Newer version: mark for update
                candidate.is_update = True
                candidate.previous_arxiv_id = existing["arxiv_id"]
                candidate.existing_paper_dir = existing["paper_dir"]
                candidate.existing_meta = existing["meta"]

        candidates.append(candidate)

    return candidates


def archive_scored_paper(
    candidate: CandidatePaper,
    score_result: dict,
) -> Path | None:
    """Write a brief meta.json under archive/papers_brief/<numeric-id>/.

    Used by the digest orchestrator after Step 2 (LLM scoring): for each
    candidate that scored >= 4, persist its evaluated metadata so subsequent
    runs (and the digest pipeline itself) can find it.

    Score < 4 papers are dropped (return None).

    If the candidate is `is_existing` (same version already archived), no
    new file is written — the existing meta is the source of truth.

    Returns the meta.json path written, or None if skipped.
    """
    if candidate.is_existing:
        # Already in archive/papers/ — no new brief needed
        return None

    score = float(score_result.get("score", 0))
    if score < 4:
        return None

    paper = candidate.paper
    clean_id = _clean_arxiv_id(paper.arxiv_id)
    # Slug = numeric arxiv id without the dot, matching existing brief layout
    slug = clean_id.replace(".", "")
    year = int(paper.published[:4]) if paper.published else 2026

    out_dir = PAPERS_BRIEF_DIR / str(year) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta.json"

    now = datetime.now(timezone.utc).isoformat()
    paper_id = hashlib.md5(paper.arxiv_id.encode("utf-8")).hexdigest()[:12]

    meta = {
        "id": paper_id,
        "title": paper.title,
        "slug": slug,
        "year": year,
        "authors": list(paper.authors)[:10],
        "affiliations": list(paper.affiliations),
        "abstract": paper.abstract,
        "arxiv_id": paper.arxiv_id,
        "tags": list(score_result.get("tags") or []),
        "category": _coerce_category(score_result.get("category", ["other"])),
        "one_line_summary": score_result.get("summary_zh", ""),
        "one_line_summary_en": score_result.get("summary_en", ""),
        "keywords": list(score_result.get("keywords") or []),
        "score": int(score),
        "score_reason": score_result.get("score_reason", ""),
        "deeply_read": False,
        "skip_reason": score_result.get("skip_reason", ""),
        "read_status": "unread",
        "rating": None,
        "generated_by": "claude-runner-script",
        "model_name": score_result.get("model_name", "") or "",
        "published_date": paper.published[:10] if paper.published else "",
        "reading_score": 0.0,
        "reading_score_reason": "",
        "url": f"https://arxiv.org/abs/{clean_id}",
        "date_added": now,
        "date_modified": now,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta_path


def candidates_to_json(candidates: list[CandidatePaper]) -> list[dict]:
    """Convert candidates to JSON-serializable list for LLM scoring."""
    out = []
    for c in candidates:
        item = {
            "arxiv_id": c.paper.arxiv_id,
            "title": c.paper.title,
            "authors": c.paper.authors[:5],
            "abstract": c.paper.abstract,
            "categories": c.paper.categories,
            "pdf_url": c.paper.pdf_url,
            "published": c.paper.published,
            "published_date": c.paper.published[:10] if c.paper.published else "",
            "is_existing": c.is_existing,
            "is_update": c.is_update,
            "previous_arxiv_id": c.previous_arxiv_id,
            "existing_paper_dir": c.existing_paper_dir,
        }
        # If existing, include the cached score/category from existing meta
        if c.is_existing and c.existing_meta:
            item["score"] = c.existing_meta.get("score", 0)
            item["category"] = _coerce_category(c.existing_meta.get("category", []))
            item["model_name"] = c.existing_meta.get("model_name", "")
            item["reading_score"] = c.existing_meta.get("reading_score", 0)
            item["one_line_summary"] = c.existing_meta.get("one_line_summary", "")
            item["slug"] = c.existing_meta.get("slug", "")
            item["year"] = c.existing_meta.get("year", 2026)
        out.append(item)
    return out
