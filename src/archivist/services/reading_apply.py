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
    "authors", "affiliations", "category", "paradigm",
    "score", "score_reason",
    "one_line_summary", "one_line_summary_en",
    "keywords", "tags", "model_name",
    "reading_score", "reading_score_reason",
    "published_date", "generated_by",
]


def apply_reading(data: dict) -> dict:
    """Apply a deep-read result. Returns a summary of what was updated.

    Schema: see .claude/skills/read-paper/update-data-schema.md
    """
    arxiv_id = data["arxiv_id"]
    paper_dir = Path(data["paper_dir"])
    slug = paper_dir.name

    summary: dict = {"arxiv_id": arxiv_id, "slug": slug}

    meta_updates = {
        k: v for k, v in data.get("meta", {}).items()
        if k in _META_FIELDS
    }
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
            paradigm=entry.get("paradigm", ""),
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
            paradigm=dag_data.get("paradigm", ""),
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
