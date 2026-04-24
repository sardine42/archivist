"""Model iteration graph: citations + performance comparisons.

Each model node corresponds to one paper. The graph has two types of edges:
- Citations (factual): model A's paper cites model B's paper
- Comparisons (agent-judged): target model outperforms source model on dataset X

Priority rules for comparison conflicts:
- is_self_reported=True (paper's own model vs baselines): HIGH priority
- is_self_reported=False (between historical models): LOW priority
- Conflict between two high-priority edges: newer paper_date wins
"""

from datetime import datetime, timezone
from pathlib import Path

from archivist.config import MODEL_GRAPH_DIR
from archivist.models import CitationEdge, DAGEdge, DAGNode, ModelGraph
from archivist.utils import read_json, write_json, write_text


GRAPH_FILE = MODEL_GRAPH_DIR / "graph.json"
CONFLICTS_FILE = MODEL_GRAPH_DIR / "conflicts.md"


def load_graph() -> ModelGraph:
    """Load the model graph from disk."""
    if GRAPH_FILE.exists():
        return ModelGraph.from_dict(read_json(GRAPH_FILE))
    return ModelGraph()


def save_graph(graph: ModelGraph) -> None:
    """Save the model graph to disk."""
    graph.last_updated = datetime.now(timezone.utc).isoformat()
    MODEL_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    write_json(GRAPH_FILE, graph.to_dict())


def add_node(
    graph: ModelGraph, name: str, paper_id: str = "", paper_title: str = "",
    description: str = "", category: list[str] | None = None,
) -> None:
    """Add a model node to the graph (or update if exists)."""
    cat = list(category or [])
    if name not in graph.nodes:
        graph.nodes[name] = DAGNode(
            model_name=name,
            paper_id=paper_id,
            paper_title=paper_title,
            description=description,
            category=cat,
        )
    else:
        node = graph.nodes[name]
        if paper_id and not node.paper_id:
            node.paper_id = paper_id
        if paper_title and not node.paper_title:
            node.paper_title = paper_title
        if cat:
            node.category = sorted(set(node.category) | set(cat))


# ── Citations (model A's paper cites model B's paper) ──────


def add_citation(graph: ModelGraph, source: str, target: str) -> bool:
    """Add a citation edge between models. Returns False if duplicate."""
    for c in graph.citations:
        if c.source == source and c.target == target:
            return False
    graph.citations.append(CitationEdge(source=source, target=target))
    return True


def add_model_with_citations(
    graph: ModelGraph,
    model_name: str,
    paper_id: str = "",
    paper_title: str = "",
    category: list[str] | None = None,
    cites_papers: list[str] | None = None,
) -> int:
    """Register a model and add citation edges to models whose papers are cited.

    Args:
        model_name: the model proposed in this paper
        paper_id: arxiv ID
        paper_title: paper title
        category: 子集 {"generative-rec", "discriminative-rec"}，通用架构可双值
        cites_papers: list of arxiv IDs cited by this paper

    Returns number of new citation edges added.
    """
    add_node(graph, model_name, paper_id=paper_id, paper_title=paper_title, category=category)

    # Build paper_id → model_name lookup from existing nodes
    paper_to_model = {n.paper_id: n.model_name for n in graph.nodes.values() if n.paper_id}

    added = 0
    for cited_paper_id in (cites_papers or []):
        cited_model = paper_to_model.get(cited_paper_id)
        if cited_model and cited_model != model_name:
            if add_citation(graph, source=model_name, target=cited_model):
                added += 1
    return added


def get_model_citations(graph: ModelGraph, model_name: str) -> tuple[list[str], list[str]]:
    """Get citation relationships for a model.

    Returns (cites, cited_by) where each is a list of model names.
    """
    cites = [c.target for c in graph.citations if c.source == model_name]
    cited_by = [c.source for c in graph.citations if c.target == model_name]
    return cites, cited_by


# ── Model Comparisons ──────────────────────────────────────


def add_edge(
    graph: ModelGraph,
    source: str,
    target: str,
    paper_id: str,
    paper_date: str = "",
    summary: str = "",
    datasets: dict[str, str] | None = None,
    is_self_reported: bool = False,
) -> list[str]:
    """Add or update an edge. One edge per (source, target) pair.

    Args:
        source: inferior model (edge start)
        target: superior model (edge end)
        paper_id: arxiv ID of the paper reporting this
        paper_date: paper date for priority resolution (YYYY-MM-DD)
        summary: overall summary of why target > source
        datasets: {dataset_name: per-dataset comparison description}
        is_self_reported: True if this is the paper's own model vs a baseline

    Returns list of conflict messages (empty if none).
    """
    # Source is a baseline — its own paper is unknown from this call site.
    # Only the target of a self-reported edge is the current paper's own
    # proposed model; assigning paper_id to anything else lets baseline
    # nodes inherit the citing paper's id and corrupts reverse lookups.
    add_node(graph, source)
    add_node(graph, target, paper_id if is_self_reported else "")

    # Check for conflicts: reverse edge
    conflicts = []
    for existing in graph.edges:
        if existing.source == target and existing.target == source:
            conflict_msg = _resolve_conflict(existing, paper_id, summary, is_self_reported, paper_date)
            conflicts.append(conflict_msg)
            _append_conflict(conflict_msg)

    # Find existing edge for this pair, or create new
    existing_edge = next(
        (e for e in graph.edges if e.source == source and e.target == target),
        None,
    )
    if existing_edge:
        # Merge datasets into existing edge
        if datasets:
            existing_edge.datasets.update(datasets)
        if summary:
            existing_edge.summary = summary
    else:
        graph.edges.append(DAGEdge(
            source=source,
            target=target,
            paper_id=paper_id,
            paper_date=paper_date,
            is_self_reported=is_self_reported,
            summary=summary,
            datasets=datasets or {},
        ))

    return conflicts


def _resolve_conflict(existing: DAGEdge, new_paper_id: str, new_summary: str,
                      new_self_reported: bool, new_date: str) -> str:
    """Resolve and format a conflict between contradictory edges."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if new_self_reported and not existing.is_self_reported:
        resolution = f"新边优先（self-reported from {new_paper_id}），旧边为历史模型间比较"
    elif existing.is_self_reported and not new_self_reported:
        resolution = f"旧边优先（self-reported from {existing.paper_id}），新边为历史模型间比较"
    elif new_self_reported and existing.is_self_reported:
        old_date = existing.paper_date or "unknown"
        if (new_date or "unknown") >= old_date:
            resolution = f"新边优先（更新的论文 {new_paper_id} @ {new_date}）"
        else:
            resolution = f"旧边优先（更新的论文 {existing.paper_id} @ {old_date}）"
    else:
        resolution = "两条边均为历史模型间比较，均保留，无明确优先级"

    msg = (
        f"## {today}: {existing.target} vs {existing.source}\n\n"
        f"**旧边**: {existing.source}→{existing.target} (Paper {existing.paper_id})\n"
        f"  {existing.summary}\n\n"
        f"**新边**: 方向相反 (Paper {new_paper_id})\n"
        f"  {new_summary}\n\n"
        f"**结论**: {resolution}\n"
    )
    return msg


def _append_conflict(message: str) -> None:
    """Append a conflict to conflicts.md."""
    MODEL_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFLICTS_FILE.exists():
        write_text(CONFLICTS_FILE, "# Model Graph Conflicts\n\n")
    with open(CONFLICTS_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def get_edges_for_dataset(graph: ModelGraph, dataset: str) -> list[DAGEdge]:
    """Get all edges that include a specific dataset."""
    return [e for e in graph.edges if dataset in e.datasets]


def get_datasets(graph: ModelGraph) -> list[str]:
    """Get all unique datasets in the graph."""
    ds = set()
    for e in graph.edges:
        ds.update(e.datasets.keys())
    return sorted(ds)


def get_conflicts() -> str:
    """Read the conflicts file."""
    if CONFLICTS_FILE.exists():
        return CONFLICTS_FILE.read_text(encoding="utf-8")
    return "No conflicts recorded."
