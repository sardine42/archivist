"""Apply a deep-read result to a paper: meta + benchmarks + DAG + progress."""

import json
from pathlib import Path

from archivist.config import ARCHIVE_ROOT
from archivist.services import paper_store
from archivist.services.benchmark import add_result, BenchmarkEntry
from archivist.services.dag import (
    add_edge,
    add_model_with_citations,
    load_graph,
    save_graph,
)


_META_FIELDS = [
    "authors", "affiliations", "category",
    "score", "score_reason",
    "one_line_summary", "one_line_summary_en",
    "tags", "proposed_tags", "model_name",
    "reading_score", "reading_score_reason",
    "published_date", "generated_by", "reasoning_effort",
    "url",
]


def _abs_url(arxiv_id: str) -> str:
    """Canonical ArXiv abs URL, version suffix stripped."""
    clean = arxiv_id.split("v")[0] if "v" in arxiv_id and arxiv_id.split("v")[-1].isdigit() else arxiv_id
    return f"https://arxiv.org/abs/{clean}"


def _normalize_category(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return list(value)


def apply_reading(data: dict) -> dict:
    """Apply a deep-read result. Returns a summary of what was updated.

    Schema: see .agents/skills/read-paper/update-data-schema.md
    """
    arxiv_id = data["arxiv_id"]
    paper_dir = Path(data["paper_dir"])
    slug = paper_dir.name

    summary: dict = {"arxiv_id": arxiv_id, "slug": slug}

    raw_meta = data.get("meta", {})
    meta_updates = {k: v for k, v in raw_meta.items() if k in _META_FIELDS}

    # Anything the caller sent that we don't persist is a typo or a schema drift
    # — report it instead of silently dropping fields when the schema drifts.
    dropped = sorted(set(raw_meta) - set(_META_FIELDS))
    if dropped:
        print(f"  ⚠ apply-reading: ignored unknown meta field(s) {dropped}", flush=True)

    # url is fully derivable from arxiv_id, so never depend on it being sent.
    # Only fill a blank: a paper imported from a non-arxiv source may carry a
    # deliberately different link, and update_paper_at overwrites outright.
    if not meta_updates.get("url"):
        try:
            stored_url = json.loads((paper_dir / "meta.json").read_text(encoding="utf-8")).get("url", "")
        except (OSError, json.JSONDecodeError):
            stored_url = ""
        if not stored_url:
            meta_updates["url"] = _abs_url(arxiv_id)
        else:
            meta_updates.pop("url", None)
    if "category" in meta_updates:
        meta_updates["category"] = _normalize_category(meta_updates["category"])
    if "tags" in meta_updates:
        from archivist.services.tag_registry import suggest_similar, validate_tags
        valid, unknown = validate_tags(list(meta_updates["tags"] or []))
        if unknown:
            hints = "; ".join(
                f"{t!r}: try {', '.join(suggest_similar(t)) or '(no close match)'}"
                for t in unknown
            )
            raise ValueError(
                f"apply-reading rejected: unknown tag(s) {unknown}. {hints}"
            )
        meta_updates["tags"] = valid
    meta_updates["deeply_read"] = True
    meta_updates["read_status"] = "read"

    paper_store.update_paper_at(paper_dir, **meta_updates)
    summary["meta_updated"] = True

    benchmarks = data.get("benchmarks", [])
    bench_conflicts: list[str] = []
    for entry in benchmarks:
        c = add_result(entry["dataset"], BenchmarkEntry(
            model=entry["model"],
            paper_id=arxiv_id,
            metrics=entry["metrics"],
            category=entry.get("category", ""),
            is_proposed_model=entry.get("is_proposed", False),
        ))
        if c:
            bench_conflicts.append(c)
    summary["benchmarks_added"] = len(benchmarks)
    summary["benchmark_conflicts"] = bench_conflicts

    dag_data = data.get("dag")
    dag_conflicts: list[str] = []
    dag_edges_added = 0
    if dag_data and dag_data.get("model_name"):
        graph = load_graph()
        new_cites = add_model_with_citations(
            graph,
            model_name=dag_data["model_name"],
            paper_id=arxiv_id,
            paper_title=dag_data.get("paper_title", ""),
            category=_normalize_category(dag_data.get("category")),
            cites_papers=dag_data.get("cites_papers", []),
        )
        summary["dag_model"] = dag_data["model_name"]
        summary["dag_new_citations"] = new_cites

        for edge in dag_data.get("edges", []):
            conflicts = add_edge(
                graph,
                source=edge["source"],
                target=edge["target"],
                paper_id=arxiv_id,
                paper_date=dag_data.get("paper_date", ""),
                summary=edge.get("summary", ""),
                datasets=edge.get("datasets", {}),
                is_self_reported=edge.get("is_self_reported", False),
            )
            if conflicts:
                dag_conflicts.extend(conflicts)
            dag_edges_added += 1

        save_graph(graph)
    summary["dag_edges_added"] = dag_edges_added
    summary["dag_conflicts"] = dag_conflicts

    progress_path = ARCHIVE_ROOT / ".reread-progress.json"
    if progress_path.exists():
        progress = json.loads(progress_path.read_text())
        for p in progress.get("papers", []):
            if p["arxiv_id"] == arxiv_id:
                p["status"] = "done"
                break
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2))
        summary["progress_updated"] = True

    return summary
