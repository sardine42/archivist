"""Paper-centred, read-only projection of the archived model graph.

The archive records citations and reported experimental comparisons, rather
than a verified inheritance tree. This projection preserves those meanings
while combining model variants that belong to the same paper.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from hashlib import sha256
import json
import math
import re
from typing import Callable

from archivist.web.titles import format_paper_title


def _paper_id(value: str | None) -> str:
    """Strip only an ArXiv version suffix, leaving legacy IDs intact."""
    return re.sub(r"v\d+$", "", (value or "").strip())


def _list(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    return list(value or [])


def _number(value) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _published_date(value: str | None) -> str:
    try:
        return date.fromisoformat((value or "")[:10]).isoformat()
    except ValueError:
        return ""


def prepare_graph_explorer(
    graph: dict,
    paper_meta: dict[str, dict],
    reading_urls: dict[str, str],
    normalize_company: Callable[[list[str]], str],
    *,
    as_of: str | None = None,
) -> dict:
    """Return a website-only projection without changing archive metadata.

    Paper IDs and reading URLs are normalised independently of the legacy
    graph payload. All papers with a reading page are included, including
    papers for which no graph relationships have been recorded yet.
    """
    meta_by_id = {_paper_id(pid): meta for pid, meta in paper_meta.items()}
    urls_by_id = {_paper_id(pid): url for pid, url in reading_urls.items()}
    groups = defaultdict(list)
    alias_to_id = {}

    for name, node in graph.get("nodes", {}).items():
        pid = _paper_id(node.get("paper_id"))
        node_id = f"paper:{pid}" if pid else f"model:{name}"
        groups[node_id].append((name, node))
        alias_to_id[name] = node_id

    # A reading page is the availability signal, matching the paper list.
    for pid in urls_by_id:
        groups.setdefault(f"paper:{pid}", [])

    nodes = []
    for node_id, originals in groups.items():
        pid = node_id.removeprefix("paper:") if node_id.startswith("paper:") else ""
        meta = meta_by_id.get(pid, {})
        source = originals[0][1] if originals else {}
        aliases = list(dict.fromkeys(
            value for name, node in originals
            for value in (name, node.get("model_name", "")) if value
        ))
        model_name = (meta.get("model_name") or "").strip()
        if model_name and model_name not in aliases:
            aliases.append(model_name)
        # Papers without a named method retain their real title as the label.
        label = model_name or next(iter(aliases), "") or meta.get("title", "") or pid
        title = meta.get("title") or source.get("paper_title") or label
        title_model = model_name if meta else next(iter(aliases), "")
        category = _list(meta.get("category")) if meta else sorted({
            category for _, node in originals for category in _list(node.get("category"))
        })
        tags = _list(meta.get("tags")) if meta else _list(source.get("tags"))
        affiliations = _list(meta.get("affiliations")) if meta else _list(source.get("affiliations"))
        reading_url = urls_by_id.get(pid, "")
        nodes.append({
            "id": node_id,
            "model_name": label,
            "aliases": aliases,
            "title": title,
            "title_html": str(format_paper_title(title, title_model)),
            "summary": meta.get("one_line_summary") or source.get("summary") or source.get("description", ""),
            "published_date": _published_date(meta.get("published_date")),
            "reading_url": reading_url,
            "arxiv_url": f"https://arxiv.org/abs/{pid}" if pid else "",
            "category": list(dict.fromkeys(category)),
            "company": normalize_company(affiliations) if affiliations else source.get("company", ""),
            "tags": list(dict.fromkeys(tags)),
            "reading_score": _number(meta.get("reading_score", source.get("reading_score"))),
            "score": _number(meta.get("score", source.get("score"))),
            "has_reading": bool(reading_url),
        })

    # Merge duplicate relationships introduced by combining same-paper models.
    # Different reporting papers retain separate experimental evidence.
    relations_by_key = {}
    removed_missing = 0
    removed_self = 0
    merged_relations = 0
    for relation_type, records in (
        ("citation", graph.get("citations", [])),
        ("comparison", graph.get("edges", [])),
    ):
        for record in records:
            source_id = alias_to_id.get(record.get("source"))
            target_id = alias_to_id.get(record.get("target"))
            if not source_id or not target_id:
                removed_missing += 1
                continue
            if source_id == target_id:
                removed_self += 1
                continue
            pid = _paper_id(record.get("paper_id")) if relation_type == "comparison" else (
                source_id.removeprefix("paper:") if source_id.startswith("paper:") else ""
            )
            key = (relation_type, source_id, target_id, pid)
            if key in relations_by_key:
                merged_relations += 1
                existing = relations_by_key[key]
                summary = record.get("summary", "")
                if summary and summary not in existing["summary"]:
                    existing["summary"] = "; ".join(filter(None, (existing["summary"], summary)))
                for dataset, description in record.get("datasets", {}).items():
                    previous = existing["datasets"].get(dataset, "")
                    if description and description not in previous:
                        existing["datasets"][dataset] = "; ".join(filter(None, (previous, description)))
                existing["is_self_reported"] |= bool(record.get("is_self_reported"))
                continue
            digest = sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
            relations_by_key[key] = {
                "id": f"{relation_type}:{digest}",
                "source": source_id,
                "target": target_id,
                "type": relation_type,
                "summary": record.get("summary", ""),
                "datasets": dict(record.get("datasets", {})),
                "is_self_reported": bool(record.get("is_self_reported", False)),
                "paper_id": pid,
            }

    nodes.sort(key=lambda node: (node["model_name"].casefold(), node["id"]))
    nodes_by_id = {node["id"]: node for node in nodes}

    def chronological(node_ids: list[str]) -> list[str]:
        return sorted(node_ids, key=lambda node_id: (
            nodes_by_id[node_id]["published_date"] or "9999-99-99",
            nodes_by_id[node_id]["model_name"].casefold(),
        ))

    lineages = [{
        "id": "semantic-id",
        "label": "SID · 语义 ID",
        "kind": "topic",
        "description": "收录带 semantic-id 主题标签的论文；同一研究方向不代表直接继承关系。",
        "members": chronological([node["id"] for node in nodes if "semantic-id" in node["tags"]]),
        "related_members": [],
    }]
    for family, label in (("onerec", "OneRec"), ("onetrans", "OneTrans"), ("onesearch", "OneSearch"), ("hstu", "HSTU")):
        # Names are explicit evidence of membership; a title-only mention is
        # related work, rather than an inferred successor in the same series.
        pattern = re.compile(r"(?<!\w)(?:Open|Mini)?" + re.escape(label) + r"(?!\w)", re.IGNORECASE)
        members = [node["id"] for node in nodes if any(
            pattern.search(name) for name in [node["model_name"], *node["aliases"]]
        )]
        if family != "onerec" and sum(nodes_by_id[node_id]["has_reading"] for node_id in members) < 2:
            continue
        related = [node["id"] for node in nodes if node["id"] not in members and pattern.search(node["title"])]
        lineages.append({
            "id": family,
            "label": label,
            "kind": "series",
            "description": f"模型名明确包含 {label} 的系列论文；仅在标题提及的论文列为相关工作，连线仍以引用和实验比较为准。",
            "members": chronological(members),
            "related_members": chronological(related),
        })

    relations = sorted(relations_by_key.values(), key=lambda relation: relation["id"])
    return {
        "nodes": nodes,
        "relations": relations,
        "lineages": lineages,
        "as_of": as_of or date.today().isoformat(),
        "stats": {
            "nodes": len(nodes),
            "read_papers": sum(node["has_reading"] for node in nodes),
            "reference_nodes": sum(not node["has_reading"] for node in nodes),
            "relations": len(relations),
            "citations": sum(relation["type"] == "citation" for relation in relations),
            "comparisons": sum(relation["type"] == "comparison" for relation in relations),
            "added_reading_papers": sum(not originals and bool(urls_by_id.get(node_id.removeprefix("paper:"))) for node_id, originals in groups.items()),
            "merged_model_variants": sum(max(0, len(originals) - 1) for originals in groups.values()),
            "removed_missing_relations": removed_missing,
            "removed_self_relations": removed_self,
            "merged_relations": merged_relations,
        },
    }
