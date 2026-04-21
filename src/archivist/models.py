"""Data models for the archive."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PaperMeta:
    id: str
    title: str
    slug: str
    year: int
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    arxiv_id: str | None = None
    source_filename: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = "other"  # "generative-rec" / "discriminative-rec" / "llm" / "other"
    one_line_summary: str = ""           # 中文总结（含核心假设、方案、实验结果）
    one_line_summary_en: str = ""        # English summary (hypothesis, method, results)
    keywords: list[str] = field(default_factory=list)
    is_generative_rec: bool = False
    score: float = 0.0
    score_reason: str = ""               # 内部排查用，不展示
    deeply_read: bool = False
    skip_reason: str | None = None
    digest_date: str | None = None
    notes: str = ""
    read_status: str = "unread"  # "unread" / "reading" / "read"
    rating: int | None = None            # 人工评分 (1-10)，"对我多有用"；见 refine-rubric skill
    rating_reason: str = ""
    feedback_consumed: bool = False      # refine-rubric 已处理（采纳或明确跳过均置 True）
    generated_by: str = ""               # 生成此文档的模型，如 "claude-opus-4-6"
    model_name: str = ""                 # 论文提出的核心模型/方法缩写
    published_date: str = ""             # 论文发布日期 (YYYY-MM-DD)
    reading_score: float = 0.0           # 精读评分 (agent 产出, 1-10)
    reading_score_reason: str = ""       # 内部排查用，不展示
    paradigm: str = ""                   # "generative" / "discriminative"
    url: str = ""                        # ArXiv 链接
    date_added: str = field(default_factory=_now)
    date_modified: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PaperMeta":
        # Filter to only known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class DocMeta:
    id: str
    title: str
    slug: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""
    generated_by: str = ""               # 生成此文档的模型
    date_created: str = field(default_factory=_now)
    date_modified: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DocMeta":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class DigestMeta:
    """Metadata for a daily/weekly/monthly digest report."""
    id: str                           # "2026-04-09" / "2026-W15" / "2026-04"
    period_type: str                  # "daily" / "weekly" / "monthly"
    title: str                        # human-readable title
    period_start: str                 # YYYY-MM-DD
    period_end: str                   # YYYY-MM-DD
    paper_count: int = 0
    deeply_read_count: int = 0
    by_category: dict = field(default_factory=dict)  # {category: [arxiv_ids]}
    highlights: list[str] = field(default_factory=list)  # arxiv_ids
    theme: str = ""
    summary: str = ""
    theme_tags: list[str] = field(default_factory=list)
    date_created: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DigestMeta":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class CitationEdge:
    """A citation relationship between models (derived from paper citations).

    source model's paper cites target model's paper.
    """
    source: str   # citing model name
    target: str   # cited model name

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DAGNode:
    """A model node. Each model corresponds to one paper."""
    model_name: str
    paper_id: str = ""        # arxiv_id of the paper proposing this model
    paper_title: str = ""     # paper title
    description: str = ""
    paradigm: str = ""        # "generative" / "discriminative"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DAGEdge:
    """A directed edge: target is generally superior to source.

    One edge per (source, target) pair. The datasets dict records
    per-dataset comparison summaries from the agent.
    """
    source: str                                    # 起点模型 (通常较差)
    target: str                                    # 终点模型 (通常较优)
    paper_id: str
    paper_date: str = ""                           # 论文日期 (YYYY-MM-DD)，用于冲突时比较新旧
    is_self_reported: bool = False                 # True = 当前论文提出的模型 vs 历史模型 (高优先级)
    summary: str = ""                              # 总体总结：为什么 target 优于 source
    datasets: dict[str, str] = field(default_factory=dict)  # {dataset: 该数据集上的对比描述}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModelGraph:
    """Two-layer graph: paper citations (factual) + model comparisons (agent-judged)."""
    nodes: dict[str, DAGNode] = field(default_factory=dict)
    edges: list[DAGEdge] = field(default_factory=list)
    citations: list[CitationEdge] = field(default_factory=list)
    last_updated: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "citations": [c.to_dict() for c in self.citations],
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelGraph":
        # Migrate old DAGNode format: rename first_seen_paper → paper_id
        nodes = {}
        for k, v in data.get("nodes", {}).items():
            if "first_seen_paper" in v and "paper_id" not in v:
                v["paper_id"] = v.pop("first_seen_paper")
            v.pop("first_seen_paper", None)
            nodes[k] = DAGNode(**{f: v[f] for f in DAGNode.__dataclass_fields__ if f in v})
        raw_edges = []
        for e in data.get("edges", []):
            # Migrate old single-dataset format to datasets dict
            if "dataset" in e and "datasets" not in e:
                ds = e.pop("dataset")
                e["datasets"] = {ds: e.get("summary", "")} if ds else {}
            raw_edges.append(DAGEdge(**{f: e[f] for f in DAGEdge.__dataclass_fields__ if f in e}))
        edges = raw_edges
        citations = [CitationEdge(**c) for c in data.get("citations", [])]
        return cls(
            nodes=nodes, edges=edges,
            citations=citations,
            last_updated=data.get("last_updated", _now()),
        )
