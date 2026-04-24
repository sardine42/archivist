"""Data preparation for web rendering (graph + benchmark + digests)."""

import json
import re
from pathlib import Path

from archivist.config import PAPERS_DIR, BENCHMARKS_DIR
from archivist.services.dag import load_graph, get_datasets
from archivist.services.benchmark import list_datasets, get_leaderboard, is_standard_metric
from archivist.services.digest import load_digests, load_digest

_company_rules_cache = None


def _load_company_rules() -> list[tuple[list[str], str]]:
    """Load company normalization rules from config.yaml.

    Cached after first load. Format: [(keywords_list, display_name), ...]
    """
    global _company_rules_cache
    if _company_rules_cache is not None:
        return _company_rules_cache
    from archivist.config import load_config
    config = load_config()
    rules = []
    for entry in config.get("companies", []):
        name = entry.get("name", "")
        keywords = entry.get("keywords", [])
        if name and keywords:
            rules.append((keywords, name))
    _company_rules_cache = rules
    return rules


def normalize_company(affiliations: list[str]) -> str:
    aff_str = " ".join(affiliations).lower()
    for keywords, company in _load_company_rules():
        if any(k.lower() in aff_str for k in keywords):
            return company
    return ""


def _clean_arxiv_id(aid: str) -> str:
    return aid.split("v")[0] if "v" in aid and aid.split("v")[-1].isdigit() else aid


def _normalize_cat(value) -> list[str]:
    """Coerce category value (legacy str or new list) to a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return list(value)


def _build_reading_urls() -> dict[str, str]:
    """Build arxiv_id -> /reading/<year>/<slug>/ mapping."""
    urls = {}
    if not PAPERS_DIR.exists():
        return urls
    for paper_dir in PAPERS_DIR.glob("*/*"):
        meta_file = paper_dir / "meta.json"
        reading_file = paper_dir / "reading.md"
        if not meta_file.exists() or not reading_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            aid = meta.get("arxiv_id", "")
            if aid:
                clean = _clean_arxiv_id(aid)
                urls[clean] = f"/reading/{meta.get('year', '')}/{meta.get('slug', '')}/"
        except Exception:
            pass
    return urls


def _build_paper_meta_cache() -> dict[str, dict]:
    """Build arxiv_id -> meta dict cache."""
    cache = {}
    if not PAPERS_DIR.exists():
        return cache
    for paper_dir in PAPERS_DIR.glob("*/*"):
        meta_file = paper_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            aid = meta.get("arxiv_id", "")
            if aid:
                cache[_clean_arxiv_id(aid)] = meta
        except Exception:
            pass
    return cache


def prepare_graph_data(dataset: str | None = None) -> dict:
    """Prepare graph data for web rendering."""
    graph = load_graph()
    graph_data = graph.to_dict()
    datasets = get_datasets(graph)

    if dataset:
        graph_data["edges"] = [e for e in graph_data["edges"] if dataset in e.get("datasets", {})]
        used = set()
        for e in graph_data["edges"]:
            used.add(e["source"])
            used.add(e["target"])
        graph_data["nodes"] = {k: v for k, v in graph_data["nodes"].items() if k in used}

    paper_meta_cache = _build_paper_meta_cache()
    reading_urls = _build_reading_urls()

    # Mark proposed models
    proposed = set()
    for e in graph_data["edges"]:
        if e.get("is_self_reported"):
            proposed.add(e["target"])

    for name, node in graph_data["nodes"].items():
        is_proposed = name in proposed
        node["is_proposed"] = is_proposed
        pid = node.get("paper_id", "")
        meta = paper_meta_cache.get(pid, {})
        # is_unread: model is only referenced via citation/comparison, no deep-read paper
        has_reading = bool(reading_urls.get(pid, "")) if pid else False
        node["is_unread"] = not has_reading
        # Enrich any node whose paper is in the archive — not just nodes that
        # are "proposed" via a self-reported edge. Otherwise survey/dataset
        # papers (no comparison edges) wouldn't get category/company set.
        if meta and has_reading:
            node["authors"] = meta.get("authors", [])
            node["affiliations"] = meta.get("affiliations", [])
            node["summary"] = meta.get("one_line_summary", "")
            node["summary_en"] = meta.get("one_line_summary_en", "")
            node["score"] = meta.get("score", "")
            node["reading_score"] = meta.get("reading_score", "")
            node["keywords"] = meta.get("keywords", [])
            node["tags"] = meta.get("tags", []) or []
            node["category"] = _normalize_cat(meta.get("category"))
            node["arxiv_url"] = f"https://arxiv.org/abs/{pid}" if pid else ""
            node["company"] = normalize_company(meta.get("affiliations", []))
            node["reading_url"] = reading_urls.get(pid, "")

    # Paper groups
    paper_groups = {}
    for name, node in graph_data["nodes"].items():
        pid = node.get("paper_id", "")
        if pid not in paper_groups:
            paper_groups[pid] = {"paper_id": pid, "category": _normalize_cat(node.get("category")), "nodes": [], "title": node.get("paper_title", "")}
        paper_groups[pid]["nodes"].append(name)

    # Paper to DAG node
    paper_to_dag_node = {}
    for name, node in graph_data["nodes"].items():
        if node.get("is_proposed") and node.get("paper_id"):
            paper_to_dag_node[node["paper_id"]] = name

    # Benchmarks
    benchmarks = {}
    for ds in list_datasets():
        entries = get_leaderboard(ds)
        if not entries:
            continue
        ds_entries = []
        for e in entries:
            metrics = {k: v for k, v in e.metrics.items() if is_standard_metric(k)}
            if not metrics:
                continue
            ds_entries.append({
                "model": e.model, "paper_id": e.paper_id, "category": e.category,
                "metrics": metrics, "is_proposed_model": e.is_proposed_model,
                "dag_node": paper_to_dag_node.get(e.paper_id, "") if e.is_proposed_model else (e.model if e.model in proposed else ""),
                "has_reading": e.model in proposed or (e.is_proposed_model and e.paper_id in paper_to_dag_node),
                "reading_url": reading_urls.get(e.paper_id, ""),
                "source_papers": e.source_papers or [e.paper_id],
            })
        if ds_entries:
            benchmarks[ds] = ds_entries

    # Always expose the 4 standard paper categories so the filter list aligns
    # with paper-list page even if some categories have zero nodes currently
    categories = ["generative-rec", "discriminative-rec", "llm", "other"]
    companies = sorted({n.get("company", "") for n in graph_data["nodes"].values() if n.get("company")})

    # Tag taxonomy from config (grouped) — same source as the reading list
    from archivist.config import load_config as _load_cfg
    tag_groups = (_load_cfg().get("tags", {}) or {})

    # Reverse index: paper_id → model name (used to display readable names
    # instead of arxiv ids in popups, edge details, etc.)
    pid_to_model = {n.get("paper_id"): name for name, n in graph_data["nodes"].items() if n.get("paper_id") and n.get("is_proposed")}
    # Fall back to any node if no proposed match
    for name, n in graph_data["nodes"].items():
        pid = n.get("paper_id")
        if pid and pid not in pid_to_model:
            pid_to_model[pid] = name

    return {
        "graph": graph_data,
        "benchmarks": benchmarks,
        "paper_groups": paper_groups,
        "paper_to_dag_node": paper_to_dag_node,
        "datasets": datasets,
        "categories": categories,
        "companies": companies,
        "tag_groups": tag_groups,
        "pid_to_model": pid_to_model,
        "reading_urls": reading_urls,
        "last_updated": graph.last_updated[:10],
    }


def prepare_benchmark_data(dataset: str | None = None) -> dict:
    """Prepare benchmark data for web rendering."""
    reading_urls = _build_reading_urls()
    paper_meta_cache = _build_paper_meta_cache()

    ds_list = list_datasets()
    if dataset:
        ds_list = [d for d in ds_list if d == dataset]

    result = []
    for ds in sorted(ds_list):
        entries = get_leaderboard(ds)
        if not entries:
            continue
        processed = []
        metric_set = set()
        for e in entries:
            metrics = {k: v for k, v in e.metrics.items() if is_standard_metric(k)}
            if not metrics:
                continue
            metric_set.update(metrics.keys())
            meta = paper_meta_cache.get(e.paper_id, {})
            processed.append({
                "model": e.model, "paper_id": e.paper_id, "category": e.category,
                "metrics": metrics, "is_proposed_model": e.is_proposed_model,
                "reading_url": reading_urls.get(e.paper_id, ""),
                "summary": meta.get("one_line_summary", ""),
                "paper_category": _normalize_cat(meta.get("category")),
                "company": normalize_company(meta.get("affiliations", [])) if meta else "",
                "tags": meta.get("tags", []) or [],
                # Order: primary (winning) source first, then the rest
                "source_papers": (
                    [e.paper_id] + [s for s in (e.source_papers or []) if s != e.paper_id]
                    if e.source_papers else [e.paper_id]
                ),
            })
        if not processed:
            continue

        metric_names = sorted(metric_set)
        best = {}
        for m in metric_names:
            vals = [e["metrics"].get(m) for e in processed if e["metrics"].get(m) is not None]
            if vals:
                best[m] = max(vals)

        slug = ds.lower().replace(" ", "-").replace("(", "").replace(")", "")
        result.append({
            "name": ds, "slug": slug, "entries": processed,
            "metric_names": metric_names, "best_values": best,
        })

    # Filter taxonomy — same lists as the graph/reading pages
    from archivist.config import load_config as _load_cfg
    tag_groups = (_load_cfg().get("tags", {}) or {})
    companies = sorted({e["company"] for ds in result for e in ds["entries"] if e.get("company")})
    categories = ["generative-rec", "discriminative-rec", "llm", "other"]

    # Model → original-paper index built from the DAG. Benchmark entries use
    # the citing paper's id for baselines, so the popup needs this alternate
    # lookup to show the model's actual proposing-paper info.
    graph = load_graph()
    model_index = {}
    for name, node in graph.nodes.items():
        pid = node.paper_id
        if not pid:
            continue
        meta = paper_meta_cache.get(pid, {})
        if not meta:
            continue
        cat = _normalize_cat(meta.get("category"))
        model_index[name] = {
            "paper_id": pid,
            "paper_title": node.paper_title or meta.get("title", ""),
            "category": node.category or cat,
            "company": normalize_company(meta.get("affiliations", [])),
            "summary": meta.get("one_line_summary", ""),
            "reading_url": reading_urls.get(pid, ""),
        }

    # Parse benchmark conflicts.md → {dataset_lower: {model: [block, ...]}}
    # Clean up each block: normalize metric aliases (R@5 → Recall@5, etc.),
    # recompute the "差异" line, and replace "Paper <id>" with model names.
    from archivist.services.benchmark import normalize_metrics as _norm_metrics
    pid_to_model = {info["paper_id"]: name for name, info in model_index.items() if info.get("paper_id")}

    def _name_for_pid(pid: str) -> str:
        return pid_to_model.get(pid, pid)

    def _parse_metric_line(line: str) -> tuple[str, str, dict]:
        # Format: "**已有** (Paper <arxiv_id>, baseline): <metric>=<value>, ..."
        m = re.match(r"\*\*(.+?)\*\*\s*\(Paper\s+(\S+),\s*(.+?)\):\s*(.*)", line)
        if not m:
            return "", "", {}
        side, pid, role, body = m.group(1), m.group(2), m.group(3), m.group(4)
        metrics = {}
        for pair in body.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                try:
                    metrics[k.strip()] = float(v.strip())
                except ValueError:
                    pass
        return _name_for_pid(pid), role, _norm_metrics(metrics)

    def _parse_block(body: str) -> dict | None:
        existing_label = new_label = ""
        existing_role = new_role = ""
        existing_metrics: dict[str, float] = {}
        new_metrics: dict[str, float] = {}
        for ln in body.split("\n"):
            ln = ln.rstrip()
            if ln.startswith("**已有**"):
                existing_label, existing_role, existing_metrics = _parse_metric_line(ln)
            elif ln.startswith("**新增**"):
                new_label, new_role, new_metrics = _parse_metric_line(ln)
        if not (existing_label and new_label):
            return None
        # Compute relative diffs on common metrics
        common = sorted(set(existing_metrics) & set(new_metrics))
        diffs = []
        max_rel = 0.0
        for k in common:
            ev, nv = existing_metrics[k], new_metrics[k]
            denom = max(abs(ev), abs(nv), 1e-9)
            rel = abs(ev - nv) / denom
            if rel > max_rel:
                max_rel = rel
            if rel > 1e-6:
                diffs.append({"metric": k, "existing": ev, "new": nv, "rel": rel})
        # Threshold: differences within 10% are considered noise, not a conflict
        if max_rel < 0.10 or not diffs:
            return None
        return {
            "existing_label": existing_label,
            "existing_role": existing_role,
            "new_label": new_label,
            "new_role": new_role,
            "diffs": diffs,
            "kept": existing_label,  # current resolution rule keeps existing
        }

    conflicts_idx = {}
    conflicts_file = BENCHMARKS_DIR / "conflicts.md"
    if conflicts_file.exists():
        try:
            text = conflicts_file.read_text(encoding="utf-8")
            blocks = re.split(r"^## ", text, flags=re.MULTILINE)
            for blk in blocks[1:]:
                header_end = blk.find("\n")
                if header_end < 0:
                    continue
                header = blk[:header_end]
                body = blk[header_end + 1:].strip()
                m = re.match(r"\d{4}-\d{2}-\d{2}:\s*(.+?)\s+on\s+(.+)$", header.strip())
                if not m:
                    continue
                model_name = m.group(1).strip()
                ds_name = m.group(2).strip()
                parsed = _parse_block(body)
                if parsed is None:
                    continue
                key = (ds_name.lower(), model_name)
                conflicts_idx.setdefault(key, []).append(parsed)
        except Exception:
            pass
    conflicts_by_ds = {}
    for (ds_lower, model_name), blocks in conflicts_idx.items():
        conflicts_by_ds.setdefault(ds_lower, {})[model_name] = blocks

    return {
        "datasets": result,
        "reading_urls": reading_urls,
        "categories": categories,
        "companies": companies,
        "tag_groups": tag_groups,
        "model_index": model_index,
        "pid_to_model": pid_to_model,
        "conflicts": conflicts_by_ds,
    }


# ── Model search index ────────────────────────────────────


def prepare_model_index() -> dict:
    """Build an inverted index: model name → {proposer, referring_papers, datasets}.

    Used by the frontend model-name search on Reading / Graph / Benchmark tabs.

    A model record has:
      - name / name_lower
      - proposer: the paper that proposed this model, or None if baseline-only
      - referring_papers: list of other papers whose benchmarks / DAG edges
        mention this model (deduped, proposer excluded)
      - datasets: sorted list of benchmark dataset names where the model appears

    Data sources:
      1. archive/papers/*/meta.json → model_name for proposer assignment
      2. archive/benchmarks/*.md via get_leaderboard → entries + source_papers
      3. archive/model-graph/graph.json via load_graph → nodes + edges
    """
    # 1. Scan paper metas → build arxiv_id → paper, slug → paper
    papers_by_slug: dict[str, dict] = {}
    if PAPERS_DIR.exists():
        for paper_dir in sorted(PAPERS_DIR.glob("*/*")):
            meta_file = paper_dir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            slug = m.get("slug", "")
            if not slug:
                continue
            papers_by_slug[slug] = {
                "slug": slug,
                "year": m.get("year", 2026),
                "title": m.get("title", ""),
                "model_name": (m.get("model_name") or "").strip(),
                "arxiv_id": _clean_arxiv_id(m.get("arxiv_id", "")),
                "published_date": m.get("published_date", ""),
            }
    pid_to_paper = {p["arxiv_id"]: p for p in papers_by_slug.values() if p["arxiv_id"]}

    # 2. Initialize records with proposers
    records: dict[str, dict] = {}

    def _ensure_record(name: str) -> dict | None:
        """Get-or-create the record keyed by lowercase name. Returns None for empty name."""
        key = (name or "").strip().lower()
        if not key:
            return None
        if key not in records:
            records[key] = {
                "name": name.strip(),
                "name_lower": key,
                "proposer": None,
                "referring_papers": [],
                "datasets": set(),
                "_seen_refs": set(),
            }
        return records[key]

    for p in papers_by_slug.values():
        mn = p["model_name"]
        if not mn:
            continue
        rec = _ensure_record(mn)
        if rec is None or rec["proposer"] is not None:
            continue  # first proposer wins
        rec["proposer"] = {
            "slug": p["slug"],
            "year": p["year"],
            "title": p["title"],
            "published_date": p.get("published_date", ""),
        }

    def _maybe_add_ref(rec: dict, paper: dict | None) -> None:
        if not paper:
            return
        slug = paper["slug"]
        proposer_slug = rec["proposer"]["slug"] if rec.get("proposer") else None
        if slug == proposer_slug:
            return
        if slug in rec["_seen_refs"]:
            return
        rec["_seen_refs"].add(slug)
        rec["referring_papers"].append({
            "slug": slug,
            "year": paper["year"],
            "title": paper["title"],
            "published_date": paper.get("published_date", ""),
        })

    # 3. Graph nodes → ensure every node has a record even if baseline-only
    graph = load_graph()
    for name, _node in graph.nodes.items():
        _ensure_record(name)

    # 4. Benchmark entries → add referring papers + datasets
    for ds_name in list_datasets():
        entries = get_leaderboard(ds_name)
        for e in entries:
            rec = _ensure_record(e.model or "")
            if rec is None:
                continue
            rec["datasets"].add(ds_name)
            # source_papers lists all arxiv ids that reported this entry
            sources = list(e.source_papers) if e.source_papers else [e.paper_id]
            for src in sources:
                ref_paper = pid_to_paper.get(_clean_arxiv_id(src or ""))
                _maybe_add_ref(rec, ref_paper)

    # 5. DAG edges → add referring papers
    for edge in graph.edges:
        ref_paper = pid_to_paper.get(_clean_arxiv_id(edge.paper_id or ""))
        if not ref_paper:
            continue
        for model_name in (edge.source, edge.target):
            rec = _ensure_record(model_name or "")
            if rec is not None:
                _maybe_add_ref(rec, ref_paper)

    # 6. Serialize — strip helper fields, sort referring papers by date desc,
    # sort models alphabetically
    out_models = []
    for key in sorted(records.keys()):
        rec = records[key]
        refs = sorted(
            rec["referring_papers"],
            key=lambda p: p.get("published_date") or "",
            reverse=True,
        )
        out_models.append({
            "name": rec["name"],
            "name_lower": rec["name_lower"],
            "proposer": rec["proposer"],
            "referring_papers": refs,
            "datasets": sorted(rec["datasets"]),
        })
    return {"models": out_models}


# ── Digest data ────────────────────────────────────────────


def prepare_digests_data() -> dict:
    """Prepare all digest reports for the reading sub-tab.

    Returns {
      daily: [{id, title, period_start, paper_count, theme, theme_tags, highlights_titles}, ...],
      weekly: [...],
      monthly: [...],
    }
    """
    paper_meta_cache = _build_paper_meta_cache()

    def to_card(d):
        # Resolve highlight arxiv_ids → paper info
        highlights_info = []
        for aid in d.highlights:
            clean = _clean_arxiv_id(aid)
            m = paper_meta_cache.get(clean, {})
            if m:
                highlights_info.append({
                    "arxiv_id": aid,
                    "model_name": m.get("model_name", ""),
                    "title": m.get("title", ""),
                    "year": m.get("year", 2026),
                    "slug": m.get("slug", ""),
                })
        return {
            "id": d.id,
            "title": d.title,
            "period_type": d.period_type,
            "period_start": d.period_start,
            "period_end": d.period_end,
            "paper_count": d.paper_count,
            "deeply_read_count": d.deeply_read_count,
            "theme": d.theme,
            "theme_tags": d.theme_tags,
            "summary": d.summary,
            "highlights": highlights_info,
        }

    daily = [to_card(d) for d in load_digests("daily")]
    weekly = [to_card(d) for d in load_digests("weekly")]
    monthly = [to_card(d) for d in load_digests("monthly")]
    return {"daily": daily, "weekly": weekly, "monthly": monthly}


def _clean_arxiv_id(aid: str) -> str:
    if not aid:
        return ""
    if "v" in aid:
        parts = aid.rsplit("v", 1)
        if parts[-1].isdigit():
            return parts[0]
    return aid


def build_paper_to_digests_index() -> dict[str, list[dict]]:
    """Build {clean_arxiv_id: [{id, period_type, title, theme}, ...]}.

    Used by paper detail page to show "Appears in Reports".
    A paper appears in a digest if it's in the digest's highlights.
    """
    index: dict[str, list[dict]] = {}
    for period_type in ["daily", "weekly", "monthly"]:
        for d in load_digests(period_type):
            entry = {
                "id": d.id,
                "period_type": d.period_type,
                "title": d.title,
                "theme": d.theme,
                "url": f"/reading/digest/{d.period_type}/{d.id}/",
            }
            for aid in d.highlights:
                clean = _clean_arxiv_id(aid)
                if clean not in index:
                    index[clean] = []
                index[clean].append(entry)
    return index


def render_digest_html(period_type: str, digest_id: str) -> tuple[dict | None, str]:
    """Render digest markdown to HTML for the detail page.

    Returns (meta_dict, html_content) or (None, "") if not found.
    """
    import markdown
    meta, body = load_digest(period_type, digest_id)
    if not meta:
        return None, ""

    # Render markdown (LaTeX protection not needed for digests, no math)
    html = markdown.markdown(
        body,
        extensions=["fenced_code", "tables", "toc", "attr_list"],
    )
    return meta.to_dict(), html
