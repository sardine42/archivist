"""Aggregate LLM-proposed tags across all paper meta.json files.

Backs `archivist tag list-pending / promote / alias / reject`. No persistent
index — scans the corpus on each call, mirroring `rubric list-pending`.
"""

from collections import defaultdict
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from archivist.config import PAPERS_BRIEF_DIR, PAPERS_DIR
from archivist.services import paper_store
from archivist.services.tag_registry import load_whitelist


@dataclass
class PendingTag:
    tag: str
    paper_count: int
    slugs: list[str]
    ready_to_promote: bool

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "paper_count": self.paper_count,
            "slugs": self.slugs,
            "ready_to_promote": self.ready_to_promote,
        }


def _all_meta_files() -> list[Path]:
    return list(chain(
        PAPERS_DIR.rglob("meta.json") if PAPERS_DIR.exists() else [],
        PAPERS_BRIEF_DIR.rglob("meta.json") if PAPERS_BRIEF_DIR.exists() else [],
    ))


def collect_pending(threshold: int = 3) -> list[PendingTag]:
    """Aggregate proposed_tags across all papers, sorted by frequency desc."""
    bucket: dict[str, list[str]] = defaultdict(list)
    for meta_file in _all_meta_files():
        data = paper_store.read_json(meta_file)
        slug = data.get("slug") or meta_file.parent.name
        for t in data.get("proposed_tags", []) or []:
            t = (t or "").strip()
            if t:
                bucket[t].append(slug)
    out = [
        PendingTag(
            tag=tag,
            paper_count=len(slugs),
            slugs=sorted(set(slugs)),
            ready_to_promote=len(set(slugs)) >= threshold,
        )
        for tag, slugs in bucket.items()
    ]
    out.sort(key=lambda p: (-p.paper_count, p.tag))
    return out


def promote_tag(tag: str) -> tuple[int, int]:
    """Move `tag` from proposed_tags into tags for every paper carrying it.

    Caller is responsible for first appending `tag` to config.yaml and
    invoking `tag_registry.reload_whitelist()`. Returns (papers_updated,
    config_appended_already_handled_externally) — second value reserved.
    """
    if tag not in load_whitelist():
        raise ValueError(
            f"{tag!r} is not in whitelist; add to config.yaml first then "
            "call tag_registry.reload_whitelist()"
        )
    updated = 0
    for meta_file in _all_meta_files():
        data = paper_store.read_json(meta_file)
        proposed = list(data.get("proposed_tags", []) or [])
        if tag not in proposed:
            continue
        proposed.remove(tag)
        tags = list(data.get("tags", []) or [])
        if tag not in tags:
            tags.append(tag)
        paper_store.update_paper_at(meta_file.parent, tags=tags, proposed_tags=proposed)
        updated += 1
    return updated, 0


def alias_tag(old: str, new: str) -> int:
    """Rewrite proposed_tags[old] → tags[new] across all papers.

    Use when a proposed term turns out to be a synonym of an existing tag.
    """
    if new not in load_whitelist():
        raise ValueError(f"{new!r} is not in whitelist; cannot alias to it")
    if old == new:
        return 0
    updated = 0
    for meta_file in _all_meta_files():
        data = paper_store.read_json(meta_file)
        proposed = list(data.get("proposed_tags", []) or [])
        if old not in proposed:
            continue
        proposed.remove(old)
        tags = list(data.get("tags", []) or [])
        if new not in tags:
            tags.append(new)
        paper_store.update_paper_at(meta_file.parent, tags=tags, proposed_tags=proposed)
        updated += 1
    return updated


def reject_tag(tag: str) -> int:
    """Drop a proposed tag from every paper's proposed_tags list."""
    updated = 0
    for meta_file in _all_meta_files():
        data = paper_store.read_json(meta_file)
        proposed = list(data.get("proposed_tags", []) or [])
        if tag not in proposed:
            continue
        proposed = [t for t in proposed if t != tag]
        paper_store.update_paper_at(meta_file.parent, proposed_tags=proposed)
        updated += 1
    return updated
