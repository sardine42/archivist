"""Benchmark leaderboard management.

Each dataset has its own Markdown file with a leaderboard table.
_index.json maps dataset names to filenames.

Priority rules for the same model on the same dataset:
- is_proposed_model=True (the paper that proposes this model): HIGH priority
- is_proposed_model=False (baseline reported by another paper): LOW priority
- Conflicts are logged to conflicts.md
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from archivist.config import BENCHMARKS_DIR
from archivist.utils import read_json, slugify, write_json


CONFLICTS_FILE = BENCHMARKS_DIR / "conflicts.md"

# Metric name normalization: map aliases to canonical names
METRIC_ALIASES = {
    "R@1": "Recall@1", "R@3": "Recall@3", "R@5": "Recall@5",
    "R@10": "Recall@10", "R@20": "Recall@20", "R@50": "Recall@50",
    "R@100": "Recall@100", "R@200": "Recall@200",
    "N@1": "NDCG@1", "N@3": "NDCG@3", "N@5": "NDCG@5",
    "N@10": "NDCG@10", "N@20": "NDCG@20", "N@50": "NDCG@50",
    "N@100": "NDCG@100", "N@200": "NDCG@200",
    "H@1": "HR@1", "H@3": "HR@3", "H@5": "HR@5",
    "H@10": "HR@10", "H@20": "HR@20", "H@50": "HR@50",
    "H@100": "HR@100",
    "HR@5": "Recall@5", "HR@10": "Recall@10", "HR@20": "Recall@20",
    "HR@50": "Recall@50", "HR@100": "Recall@100", "HR@200": "Recall@200",
    "HR@500": "Recall@500", "HR@1000": "Recall@1000",
    "HR@1": "Recall@1", "HR@3": "Recall@3",
}


# Standard metrics to keep (prefix match). Non-standard metrics are dropped on display.
STANDARD_METRIC_PREFIXES = (
    "Recall@", "NDCG@", "HR@", "AUC", "MRR", "MAP@",
    "BLEU", "BERTScore", "ROUGE", "LogLoss",
)


def normalize_metrics(metrics: dict[str, float]) -> dict[str, float]:
    """Normalize metric names to canonical form."""
    result = {}
    for k, v in metrics.items():
        # Fix case: RECALL@10 -> Recall@10
        if k.startswith("RECALL@"):
            k = "Recall@" + k[7:]
        canonical = METRIC_ALIASES.get(k, k)
        result[canonical] = v
    return result


def is_standard_metric(name: str) -> bool:
    """Check if a metric name is a standard/universal metric."""
    return any(name.startswith(p) for p in STANDARD_METRIC_PREFIXES)


@dataclass
class BenchmarkEntry:
    model: str
    paper_id: str  # arxiv_id (primary/winning source)
    metrics: dict[str, float]  # {"NDCG@10": 0.053, ...}
    paradigm: str = ""  # "generative" / "discriminative"
    hyperparams: str = ""
    notes: str = ""
    is_proposed_model: bool = False
    source_papers: list[str] = field(default_factory=list)  # all paper_ids that reported this model


def get_index() -> dict[str, str]:
    """Load or create the benchmark index (_index.json)."""
    index_file = BENCHMARKS_DIR / "_index.json"
    if index_file.exists():
        return read_json(index_file)
    return {}


def _save_index(index: dict[str, str]) -> None:
    write_json(BENCHMARKS_DIR / "_index.json", index)


def add_result(dataset: str, entry: BenchmarkEntry) -> str | None:
    """Add a benchmark result to a dataset's leaderboard.

    If the same model already exists from a different paper, applies priority:
    - proposed > baseline. Higher priority replaces lower.
    - Same priority: keeps existing, logs conflict.

    Returns conflict message string if conflict detected, None otherwise.
    """
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    entry.metrics = normalize_metrics(entry.metrics)

    # Enforce decimal scale: convert any value > 1 to decimal (divide by 100)
    for m in list(entry.metrics.keys()):
        if entry.metrics[m] > 1:
            entry.metrics[m] = entry.metrics[m] / 100.0

    index = get_index()
    ds_slug = slugify(dataset)
    filename = f"{ds_slug}.md"

    if dataset not in index:
        index[dataset] = filename
        _save_index(index)

    filepath = BENCHMARKS_DIR / filename
    entries = _parse_leaderboard(filepath) if filepath.exists() else []

    # Ensure source_papers is initialized
    if not entry.source_papers:
        entry.source_papers = [entry.paper_id]
    elif entry.paper_id not in entry.source_papers:
        entry.source_papers.append(entry.paper_id)

    # Check for same (model, paper_id) — simple update
    same_source = next((e for e in entries if e.model == entry.model and e.paper_id == entry.paper_id), None)
    if same_source:
        same_source.metrics.update(entry.metrics)
        same_source.hyperparams = entry.hyperparams or same_source.hyperparams
        same_source.notes = entry.notes or same_source.notes
        _write_leaderboard(filepath, dataset, entries)
        return None

    # Check for same model from a different paper — conflict
    existing = next((e for e in entries if e.model == entry.model), None)
    conflict_msg = None
    if existing:
        # Always record the new paper as a source
        if entry.paper_id not in existing.source_papers:
            existing.source_papers.append(entry.paper_id)

        conflict_msg = _resolve_benchmark_conflict(dataset, existing, entry)
        if entry.is_proposed_model and not existing.is_proposed_model:
            # New entry is proposed, existing is baseline → replace (self-reported preferred)
            # Carry over accumulated source_papers
            entry.source_papers = existing.source_papers
            if entry.paper_id not in entry.source_papers:
                entry.source_papers.append(entry.paper_id)
            entries.remove(existing)
            entries.append(entry)
        elif not entry.is_proposed_model and existing.is_proposed_model:
            # Existing is proposed, new is baseline → keep existing (self-reported preferred)
            pass
        elif entry.is_proposed_model and existing.is_proposed_model:
            # Both proposed (rare) → keep existing
            pass
        else:
            # Both baseline → keep existing
            pass
    else:
        entries.append(entry)

    _write_leaderboard(filepath, dataset, entries)
    return conflict_msg


def _resolve_benchmark_conflict(dataset: str, existing: BenchmarkEntry, new: BenchmarkEntry) -> str:
    """Format and log a benchmark conflict."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Resolve paper_id → human-readable model name via the DAG
    def _model_for_pid(pid: str) -> str:
        try:
            from archivist.services.dag import load_graph
            for n in load_graph().nodes.values():
                if n.paper_id == pid and n.model_name:
                    return n.model_name
        except Exception:
            pass
        return pid

    existing_label = _model_for_pid(existing.paper_id)
    new_label = _model_for_pid(new.paper_id)

    if new.is_proposed_model and not existing.is_proposed_model:
        resolution = f"新记录优先（proposed from {new_label}），替换 baseline 记录"
    elif existing.is_proposed_model and not new.is_proposed_model:
        resolution = f"保留已有记录（proposed from {existing_label}），丢弃 baseline 记录"
    else:
        resolution = f"优先级相同，保留已有记录（from {existing_label}）"

    # Normalize before diffing so metric aliases (R@5 vs Recall@5) align
    existing_metrics = normalize_metrics(existing.metrics)
    new_metrics = normalize_metrics(new.metrics)

    diffs = []
    common_metrics = set(existing_metrics) & set(new_metrics)
    for m in sorted(common_metrics):
        old_v, new_v = existing_metrics[m], new_metrics[m]
        if abs(old_v - new_v) > 1e-6:
            diffs.append(f"{m}: {old_v:.4f} vs {new_v:.4f}")

    msg = (
        f"## {today}: {new.model} on {dataset}\n\n"
        f"**已有** ({existing_label}, {'proposed' if existing.is_proposed_model else 'baseline'}): "
        f"{', '.join(f'{m}={v:.4f}' for m, v in existing_metrics.items())}\n\n"
        f"**新增** ({new_label}, {'proposed' if new.is_proposed_model else 'baseline'}): "
        f"{', '.join(f'{m}={v:.4f}' for m, v in new_metrics.items())}\n\n"
        f"**差异**: {', '.join(diffs) if diffs else '(无共同指标)'}\n\n"
        f"**结论**: {resolution}\n"
    )

    _append_conflict(msg)
    return msg


def _append_conflict(message: str) -> None:
    """Append a conflict to benchmark conflicts.md."""
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFLICTS_FILE.exists():
        CONFLICTS_FILE.write_text("# Benchmark Conflicts\n\n", encoding="utf-8")
    with open(CONFLICTS_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def get_leaderboard(dataset: str) -> list[BenchmarkEntry]:
    """Get the leaderboard for a dataset."""
    index = get_index()
    filename = index.get(dataset)
    if not filename:
        return []
    filepath = BENCHMARKS_DIR / filename
    if not filepath.exists():
        return []
    return _parse_leaderboard(filepath)


def list_datasets() -> list[str]:
    """List all tracked datasets."""
    return list(get_index().keys())


def _get_sort_metric(entries: list[BenchmarkEntry]) -> str | None:
    """Determine the primary metric for ranking (most common metric)."""
    from collections import Counter
    metric_counts: Counter[str] = Counter()
    for e in entries:
        for m in e.metrics:
            metric_counts[m] += 1
    if not metric_counts:
        return None
    return metric_counts.most_common(1)[0][0]


def _write_leaderboard(filepath: Path, dataset: str, entries: list[BenchmarkEntry]) -> None:
    """Write the leaderboard as Markdown tables, split by paradigm."""
    lines = [f"# Benchmark: {dataset}\n"]

    # Group by paradigm
    paradigm_order = ["generative", "discriminative"]
    paradigm_labels = {"generative": "生成式模型", "discriminative": "判别式模型"}
    grouped: dict[str, list[BenchmarkEntry]] = {}
    for e in entries:
        p = e.paradigm or "other"
        grouped.setdefault(p, []).append(e)

    # Write each paradigm as a separate table
    for paradigm in paradigm_order + [k for k in grouped if k not in paradigm_order]:
        group = grouped.get(paradigm)
        if not group:
            continue

        sort_metric = _get_sort_metric(group)
        if sort_metric:
            group.sort(key=lambda e: e.metrics.get(sort_metric, 0), reverse=True)

        # Collect metric names for this group
        all_metrics: list[str] = []
        seen: set[str] = set()
        for e in group:
            for m in e.metrics:
                if m not in seen:
                    all_metrics.append(m)
                    seen.add(m)

        label = paradigm_labels.get(paradigm, paradigm)
        lines.append(f"## {label}\n")
        header = "| Rank | Model | Paper | " + " | ".join(all_metrics) + " | Sources | Hyperparams | Notes |"
        sep = "|" + "|".join(["------"] * (6 + len(all_metrics))) + "|"
        lines.append(header)
        lines.append(sep)

        for i, e in enumerate(group):
            metrics_str = " | ".join(
                f"{e.metrics.get(m, '')}" if isinstance(e.metrics.get(m), str)
                else f"{e.metrics.get(m, 0):.4f}" if m in e.metrics
                else ""
                for m in all_metrics
            )
            proposed = " ★" if e.is_proposed_model else ""
            sources = ";".join(e.source_papers) if e.source_papers else e.paper_id
            lines.append(f"| {i+1} | {e.model}{proposed} | {e.paper_id} | {metrics_str} | {sources} | {e.hyperparams} | {e.notes} |")

        lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")


def _parse_leaderboard(filepath: Path) -> list[BenchmarkEntry]:
    """Parse a Markdown leaderboard table back into BenchmarkEntry objects.

    Supports both new format (split by ## paradigm sections) and old single-table format.
    """
    text = filepath.read_text(encoding="utf-8")
    entries = []
    lines = text.strip().split("\n")

    # Detect paradigm from ## headings
    paradigm_labels_rev = {"生成式模型": "generative", "判别式模型": "discriminative"}
    current_paradigm = ""

    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect paradigm section
        if line.startswith("## "):
            heading = line[3:].strip()
            current_paradigm = paradigm_labels_rev.get(heading, heading)
            i += 1
            continue

        # Detect table header
        if not line.startswith("| Rank"):
            i += 1
            continue

        cols = [c.strip() for c in line.split("|")[1:-1]]
        # Detect format: new has Sources column, old has Paradigm column
        has_paradigm_col = len(cols) > 3 and cols[2] == "Paradigm"
        has_sources_col = "Sources" in cols

        # Determine trailing columns count (after metrics)
        # New: ...metrics, Sources, Hyperparams, Notes (3 trailing)
        # Old: ...metrics, Hyperparams, Notes (2 trailing)
        trailing = 3 if has_sources_col else 2

        if has_paradigm_col:
            metric_names = cols[4:-trailing] if len(cols) > 4 + trailing else []
        else:
            metric_names = cols[3:-trailing] if len(cols) > 3 + trailing else []

        i += 2  # Skip header + separator
        while i < len(lines) and lines[i].startswith("|"):
            cells = [c.strip() for c in lines[i].split("|")[1:-1]]
            i += 1
            if len(cells) < 5:
                continue

            model = cells[1].replace(" ★", "")
            is_proposed = "★" in cells[1]

            if has_paradigm_col:
                paradigm = cells[2]
                paper_id = cells[3]
                metric_offset = 4
            else:
                paradigm = current_paradigm
                paper_id = cells[2]
                metric_offset = 3

            metrics = {}
            for j, mname in enumerate(metric_names):
                val_str = cells[metric_offset + j] if metric_offset + j < len(cells) else ""
                if val_str:
                    try:
                        metrics[mname] = float(val_str)
                    except ValueError:
                        pass

            # Parse source_papers, hyperparams, notes from trailing columns
            if has_sources_col:
                sources_str = cells[-3] if len(cells) >= 6 else ""
                source_papers = [s.strip() for s in sources_str.split(";") if s.strip()] if sources_str else [paper_id]
                hyperparams = cells[-2] if len(cells) >= 6 else ""
                notes = cells[-1] if len(cells) >= 6 else ""
            else:
                source_papers = [paper_id]
                hyperparams = cells[-2] if len(cells) >= 5 else ""
                notes = cells[-1] if len(cells) >= 5 else ""

            entries.append(BenchmarkEntry(
                model=model,
                paper_id=paper_id,
                metrics=metrics,
                paradigm=paradigm,
                hyperparams=hyperparams,
                notes=notes,
                is_proposed_model=is_proposed,
                source_papers=source_papers,
            ))
        continue

    return entries

    return entries
