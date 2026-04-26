"""Digest report generation: daily, weekly, monthly.

Two-phase flow per period:
1. prepare_*(...) → returns dict for agent analysis
2. write_*(period_id, agent_data) → writes Markdown + JSON to archive/digests/

Daily: scans archive/papers/ by date_added
Weekly: aggregates daily reports of the ISO week
Monthly: aggregates daily + weekly reports of the calendar month
"""

import json
from datetime import datetime, timezone, date as date_type, timedelta
from pathlib import Path

from archivist.config import DIGESTS_DIR, PAPERS_DIR, PAPERS_BRIEF_DIR
from archivist.models import DigestMeta
from archivist.utils import read_json, write_json, write_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_dir(period_type: str) -> Path:
    """Get the directory for a given period type. Year is 2026 for now."""
    return DIGESTS_DIR / "2026" / period_type


def _normalize_company(affiliations: list[str]) -> str:
    """Reuse canonical company normalization from web/data.py."""
    from archivist.web.data import normalize_company
    return normalize_company(affiliations)


# ── Daily ────────────────────────────────────────────────────


def _coverage_range(date: str) -> tuple[str, str]:
    """Compute (start, end) inclusive date range covered by a daily digest.

    Aligned with ArXiv announcement cycle (ET 20:00, Sun-Thu only — no Fri/Sat
    announcements per https://info.arxiv.org/help/availability.html).
    The digest runs Mon-Fri at 09:00 Beijing time (= prev day 21:00 ET), one
    hour after each ArXiv announcement window.

    Rule: a digest generated on date D covers ArXiv submissions from:
    - Mon → Friday only (catches the Sun ET 20:00 announcement of Thu-Fri batch)
    - Tue → Sat + Sun + Mon (catches the Mon ET 20:00 announcement of weekend batch)
    - Wed/Thu/Fri → previous calendar day only
    - Sat/Sun → not scheduled (no ArXiv announcement); if requested, covers previous day
    """
    d = date_type.fromisoformat(date)
    weekday = d.weekday()  # Mon=0, Sun=6
    if weekday == 0:  # Monday — covers Friday's submissions only
        start = d - timedelta(days=3)  # Friday
        end = start
    elif weekday == 1:  # Tuesday — covers Sat/Sun/Mon (Friday already covered by Mon cron)
        start = d - timedelta(days=3)  # Saturday
        end = d - timedelta(days=1)    # Monday
    else:
        start = d - timedelta(days=1)
        end = start
    return start.isoformat(), end.isoformat()


def prepare_daily(date: str) -> dict:
    """Scan archive/papers/ for papers published in the digest's coverage range.

    A digest generated on date D covers prior days' ArXiv submissions:
    - Mon: covers Fri (Sun ET 20:00 announcement of Thu-Fri batch)
    - Tue: covers Sat+Sun+Mon (Mon ET 20:00 announcement of weekend batch)
    - Wed-Fri: covers previous day

    Filter by published_date in the coverage range.
    Cross-day deduplication: if the paper appeared in any earlier daily digest,
    skip it here.

    Args:
        date: YYYY-MM-DD (the date the digest is generated/labeled with)
    """
    range_start, range_end = _coverage_range(date)

    # Build cross-day dedup set: arxiv_ids already in earlier daily digests
    earlier_ids = set()
    daily_dir = _digest_dir("daily")
    if daily_dir.exists():
        for jf in sorted(daily_dir.glob("*.json")):
            existing = read_json(jf)
            if existing.get("id", "") < date:
                for cat, ids in existing.get("by_category", {}).items():
                    earlier_ids.update(aid.split("v")[0] for aid in ids)

    papers_by_arxiv: dict[str, dict] = {}
    by_category: dict[str, int] = {}
    deep_read = 0
    brief_only = 0

    # Scan PAPERS_DIR first so deep-read entries take priority over brief ones
    for src_dir in [PAPERS_DIR, PAPERS_BRIEF_DIR]:
        if not src_dir.exists():
            continue
        for paper_dir in src_dir.glob("*/*"):
            meta_file = paper_dir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                m = read_json(meta_file)
            except Exception:
                continue
            pub = m.get("published_date", "")
            if not pub or pub < range_start or pub > range_end:
                continue
            arxiv_id = m.get("arxiv_id", "").split("v")[0]  # normalize: strip version suffix
            if arxiv_id in earlier_ids:
                continue
            # Skip if we already have a deep-read entry for this paper
            if arxiv_id in papers_by_arxiv:
                continue

            has_reading = (paper_dir / "reading.md").exists()

            raw_cat = m.get("category", ["other"])
            if isinstance(raw_cat, str):
                cat_list = [raw_cat] if raw_cat else ["other"]
            else:
                cat_list = list(raw_cat) or ["other"]

            papers_by_arxiv[arxiv_id] = {
                "arxiv_id": arxiv_id,
                "title": m.get("title", ""),
                "authors": m.get("authors", []),
                "affiliations": m.get("affiliations", []),
                "company": _normalize_company(m.get("affiliations", [])),
                "category": cat_list,
                "model_name": m.get("model_name", ""),
                "tags": m.get("tags", []),
                "score": m.get("score", 0),
                "reading_score": m.get("reading_score", 0),
                "one_line_summary": m.get("one_line_summary", ""),
                "one_line_summary_en": m.get("one_line_summary_en", ""),
                "deeply_read": has_reading,
                "skip_reason": m.get("skip_reason", ""),
                "slug": m.get("slug", ""),
                "year": m.get("year", 2026),
                "url": m.get("url", ""),
            }

    papers = list(papers_by_arxiv.values())
    for p in papers:
        for cat in p["category"]:
            by_category[cat] = by_category.get(cat, 0) + 1
        if p["deeply_read"]:
            deep_read += 1
        else:
            brief_only += 1

    # Sort by reading_score desc, then score desc
    papers.sort(key=lambda p: (p["reading_score"] or 0, p["score"] or 0), reverse=True)

    return {
        "date": date,
        "papers": papers,
        "stats": {
            "total": len(papers),
            "deeply_read": deep_read,
            "brief_only": brief_only,
            "by_category": by_category,
        },
    }


def write_daily(date: str, agent_data: dict) -> Path:
    """Write daily digest to disk based on agent's analysis.

    Args:
        date: YYYY-MM-DD
        agent_data: {summary, theme, theme_tags, highlights}
    """
    prep = prepare_daily(date)
    papers = prep["papers"]
    stats = prep["stats"]

    range_start, range_end = _coverage_range(date)
    title = f"{date} 日报"
    meta = DigestMeta(
        id=date,
        period_type="daily",
        title=title,
        period_start=range_start,
        period_end=range_end,
        paper_count=stats["total"],
        deeply_read_count=stats["deeply_read"],
        by_category={cat: [p["arxiv_id"] for p in papers if cat in p["category"]]
                     for cat in stats["by_category"]},
        highlights=agent_data.get("highlights", []),
        theme=agent_data.get("theme", ""),
        summary=agent_data.get("summary", ""),
        theme_tags=agent_data.get("theme_tags", []),
    )

    # Generate Markdown
    lines = [f"# {title}", ""]
    if meta.theme:
        lines.append(f"**主题**: {meta.theme}")
        lines.append("")
    if meta.theme_tags:
        lines.append("**标签**: " + " · ".join(f"`{t}`" for t in meta.theme_tags))
        lines.append("")
    industry_count = sum(1 for p in papers if p.get("company"))
    academic_count = stats["total"] - industry_count
    lines.append(f"📊 **统计**: 共 {stats['total']} 篇 · 精读 {stats['deeply_read']} · "
                 f"🏢 工业界 {industry_count} · 🎓 学术 {academic_count} · "
                 + " · ".join(f"{cat} {cnt}" for cat, cnt in stats["by_category"].items()))
    lines.append("")

    if not papers:
        lines.append("> 今日无相关论文。")
        lines.append("")
    else:
        if meta.summary:
            lines.append("## 综述")
            lines.append("")
            lines.append(meta.summary)
            lines.append("")

        if meta.highlights:
            lines.append("## 重点论文")
            lines.append("")
            # Normalize highlight set: strip version suffix
            def _strip_v(aid):
                if "v" in aid:
                    parts = aid.rsplit("v", 1)
                    if parts[-1].isdigit():
                        return parts[0]
                return aid
            highlight_set = {_strip_v(h) for h in meta.highlights}
            for p in papers:
                if _strip_v(p["arxiv_id"]) not in highlight_set:
                    continue
                _append_paper_block(lines, p)

        lines.append("## 全部论文")
        lines.append("")
        lines.append("| 模型 | 标题 | 类别 | 公司 | 摘要分 | 精读分 |")
        lines.append("|------|------|------|------|--------|--------|")
        for p in papers:
            model = p["model_name"] or "—"
            title = p["title"]
            if p["deeply_read"]:
                model_cell = f"**[{model}](/reading/{p['year']}/{p['slug']}/)**" if model != "—" else "—"
                title_cell = f"[{title}](/reading/{p['year']}/{p['slug']}/)"
            else:
                model_cell = model
                title_cell = title
            _label_map = {"generative-rec": "生成式", "discriminative-rec": "判别式",
                          "llm": "LLM", "other": "其他"}
            cat_label = " / ".join(_label_map.get(c, c) for c in p["category"])
            company = f"🏢 {p['company']}" if p["company"] else "🎓 学术"
            rs = f"{p['reading_score']:.0f}" if p["reading_score"] else "—"
            lines.append(f"| {model_cell} | {title_cell} | {cat_label} | {company} | {p['score']:.0f} | {rs} |")
        lines.append("")

    # Write files
    out_dir = _digest_dir("daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{date}.md"
    json_path = out_dir / f"{date}.json"
    write_text(md_path, "\n".join(lines))
    write_json(json_path, meta.to_dict())
    return md_path


def _append_paper_block(lines: list, p: dict):
    """Append a highlighted paper block to markdown lines."""
    model = p["model_name"]
    title = p["title"]
    rs = f"⭐ {p['reading_score']:.0f}/10" if p["reading_score"] else f"⭐ {p['score']:.0f}/10"
    # Heading: model name (large) + score
    if model:
        if p["deeply_read"]:
            lines.append(f"### [{model}](/reading/{p['year']}/{p['slug']}/) · {rs}")
        else:
            lines.append(f"### {model} · {rs}")
    else:
        # No model name, use title in heading
        if p["deeply_read"]:
            lines.append(f"### [{title}](/reading/{p['year']}/{p['slug']}/) · {rs}")
        else:
            lines.append(f"### {title} · {rs}")
    lines.append("")
    # Subtitle: full title (if model name is shown above)
    if model:
        if p["deeply_read"]:
            lines.append(f"*[{title}](/reading/{p['year']}/{p['slug']}/)*")
        else:
            lines.append(f"*{title}*")
        lines.append("")
    _label_map = {"generative-rec": "生成式推荐", "discriminative-rec": "判别式推荐",
                  "llm": "LLM", "other": "其他"}
    cat_label = " / ".join(_label_map.get(c, c) for c in p["category"])
    company = f"🏢 {p['company']}" if p["company"] else "🎓 学术"
    lines.append(f"> {company} · {cat_label}")
    lines.append(">")
    if p["one_line_summary"]:
        for sline in p["one_line_summary"].split("\n"):
            lines.append(f"> {sline}")
    lines.append("")


# ── Weekly ───────────────────────────────────────────────────


def prepare_weekly(week: str) -> dict:
    """Aggregate daily digests whose *coverage range* overlaps the ISO week.

    Why coverage-range, not digest-id-in-week: ArXiv submissions from
    Fri/Sat/Sun of week N are only deep-read on Mon of week N+1 (whose
    daily digest has ID = next-Monday, in week N+1). To correctly pull
    those papers into week N's weekly, we match by the daily digest's
    `period_start/period_end` metadata — which `write_daily` fills from
    `_coverage_range(date)`.

    Args:
        week: YYYY-Www (e.g. 2026-W15)
    """
    year, week_num = _parse_week(week)
    monday = date_type.fromisocalendar(year, week_num, 1)
    sunday = monday + timedelta(days=6)

    daily_dir = _digest_dir("daily")
    daily_reports = []
    if daily_dir.exists():
        for json_file in sorted(daily_dir.glob("*.json")):
            try:
                m = read_json(json_file)
            except Exception:
                continue
            # Match if the digest's coverage range overlaps [monday, sunday]
            try:
                cov_start = date_type.fromisoformat(m.get("period_start", ""))
                cov_end = date_type.fromisoformat(m.get("period_end", ""))
            except ValueError:
                # Older digests without period_* fall back to id-based match
                try:
                    cov_start = cov_end = date_type.fromisoformat(m.get("id", ""))
                except ValueError:
                    continue
            if cov_end < monday or cov_start > sunday:
                continue  # no overlap
            daily_reports.append({
                "date": m["id"],
                "paper_count": m.get("paper_count", 0),
                "deeply_read_count": m.get("deeply_read_count", 0),
                "theme": m.get("theme", ""),
                "summary": m.get("summary", ""),
                "highlights": m.get("highlights", []),
                "theme_tags": m.get("theme_tags", []),
                "by_category": m.get("by_category", {}),
            })

    return {
        "week": week,
        "date_range": [monday.isoformat(), sunday.isoformat()],
        "daily_reports": daily_reports,
    }


def write_weekly(week: str, agent_data: dict) -> Path:
    """Write weekly digest based on agent's analysis."""
    prep = prepare_weekly(week)
    drs = prep["daily_reports"]

    total_papers = sum(d["paper_count"] for d in drs)
    total_deep = sum(d["deeply_read_count"] for d in drs)

    title = f"{week} 周报"
    meta = DigestMeta(
        id=week,
        period_type="weekly",
        title=title,
        period_start=prep["date_range"][0],
        period_end=prep["date_range"][1],
        paper_count=total_papers,
        deeply_read_count=total_deep,
        highlights=agent_data.get("highlights", []),
        theme=agent_data.get("theme", ""),
        summary=agent_data.get("summary", ""),
        theme_tags=agent_data.get("theme_tags", []),
    )

    lines = [f"# {title}", ""]
    lines.append(f"**日期范围**: {prep['date_range'][0]} ~ {prep['date_range'][1]}")
    lines.append("")
    if meta.theme:
        lines.append(f"**主题**: {meta.theme}")
        lines.append("")
    if meta.theme_tags:
        lines.append("**标签**: " + " · ".join(f"`{t}`" for t in meta.theme_tags))
        lines.append("")
    lines.append(f"📊 **统计**: 共 {total_papers} 篇 · 精读 {total_deep} · 覆盖 {len(drs)} 个工作日")
    lines.append("")

    if not drs:
        lines.append("> 本周无任何日报数据。")
        lines.append("")
    else:
        if meta.summary:
            lines.append("## 周度综述")
            lines.append("")
            lines.append(meta.summary)
            lines.append("")

        lines.append("## 每日概览")
        lines.append("")
        for d in sorted(drs, key=lambda x: x["date"]):
            lines.append(f"### [{d['date']}](/reading/digest/daily/{d['date']}/)")
            lines.append("")
            if d["theme"]:
                lines.append(f"- **主题**: {d['theme']}")
            lines.append(f"- **论文数**: {d['paper_count']} · 精读: {d['deeply_read_count']}")
            lines.append("")

    out_dir = _digest_dir("weekly")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{week}.md"
    json_path = out_dir / f"{week}.json"
    write_text(md_path, "\n".join(lines))
    write_json(json_path, meta.to_dict())
    return md_path


def _parse_week(week: str) -> tuple[int, int]:
    """Parse 2026-W15 into (2026, 15)."""
    parts = week.split("-W")
    return int(parts[0]), int(parts[1])


# ── Monthly ──────────────────────────────────────────────────


def prepare_monthly(month: str) -> dict:
    """Aggregate daily + weekly digests of the calendar month.

    Args:
        month: YYYY-MM
    """
    year, mo = month.split("-")
    year_i, mo_i = int(year), int(mo)

    # Find date range
    first = date_type(year_i, mo_i, 1)
    if mo_i == 12:
        last = date_type(year_i + 1, 1, 1) - timedelta(days=1)
    else:
        last = date_type(year_i, mo_i + 1, 1) - timedelta(days=1)

    # Collect daily reports whose coverage range overlaps the target month.
    # (See prepare_weekly for the rationale — Monday dailies for prev week's
    # Fri/Sat/Sun papers have an ID in the next month's boundary cases.)
    daily_dir = _digest_dir("daily")
    daily_reports = []
    if daily_dir.exists():
        for json_file in sorted(daily_dir.glob("*.json")):
            try:
                m = read_json(json_file)
            except Exception:
                continue
            try:
                cov_start = date_type.fromisoformat(m.get("period_start", ""))
                cov_end = date_type.fromisoformat(m.get("period_end", ""))
            except ValueError:
                try:
                    cov_start = cov_end = date_type.fromisoformat(m.get("id", ""))
                except ValueError:
                    continue
            if cov_end < first or cov_start > last:
                continue
            daily_reports.append(m)

    # Collect weekly reports
    weekly_dir = _digest_dir("weekly")
    weekly_reports = []
    if weekly_dir.exists():
        for json_file in sorted(weekly_dir.glob("*.json")):
            try:
                m = read_json(json_file)
                week_id = m.get("id", "")
                year_w, week_n = _parse_week(week_id)
                # Include week if its monday falls in the month
                monday = date_type.fromisocalendar(year_w, week_n, 1)
                if first <= monday <= last:
                    weekly_reports.append(m)
            except Exception:
                continue

    # Stats
    total_papers = sum(d.get("paper_count", 0) for d in daily_reports)
    total_deep = sum(d.get("deeply_read_count", 0) for d in daily_reports)
    by_category: dict[str, int] = {}
    for d in daily_reports:
        for cat, ids in d.get("by_category", {}).items():
            by_category[cat] = by_category.get(cat, 0) + len(ids)

    return {
        "month": month,
        "date_range": [first.isoformat(), last.isoformat()],
        "daily_reports": daily_reports,
        "weekly_reports": weekly_reports,
        "stats": {
            "total_papers": total_papers,
            "deeply_read": total_deep,
            "by_category": by_category,
        },
    }


def write_monthly(month: str, agent_data: dict) -> Path:
    """Write monthly digest based on agent's analysis."""
    prep = prepare_monthly(month)
    stats = prep["stats"]

    title = f"{month} 月报"
    meta = DigestMeta(
        id=month,
        period_type="monthly",
        title=title,
        period_start=prep["date_range"][0],
        period_end=prep["date_range"][1],
        paper_count=stats["total_papers"],
        deeply_read_count=stats["deeply_read"],
        by_category={cat: [] for cat in stats["by_category"]},  # detailed list omitted
        highlights=agent_data.get("highlights", []),
        theme=agent_data.get("theme", ""),
        summary=agent_data.get("summary", ""),
        theme_tags=agent_data.get("theme_tags", []),
    )

    lines = [f"# {title}", ""]
    lines.append(f"**日期范围**: {prep['date_range'][0]} ~ {prep['date_range'][1]}")
    lines.append("")
    if meta.theme:
        lines.append(f"**主题**: {meta.theme}")
        lines.append("")
    lines.append(f"📊 **统计**: 共 {stats['total_papers']} 篇 · 精读 {stats['deeply_read']}")
    lines.append("")
    if stats["by_category"]:
        lines.append("**类别分布**:")
        for cat, cnt in stats["by_category"].items():
            cat_label = {"generative-rec": "生成式推荐", "discriminative-rec": "判别式推荐",
                         "llm": "LLM", "other": "其他"}.get(cat, cat)
            lines.append(f"- {cat_label}: {cnt} 篇")
        lines.append("")

    if meta.summary:
        lines.append("## 月度综述")
        lines.append("")
        lines.append(meta.summary)
        lines.append("")

    if prep["weekly_reports"]:
        lines.append("## 周度回顾")
        lines.append("")
        for w in sorted(prep["weekly_reports"], key=lambda x: x.get("id", "")):
            lines.append(f"### [{w['id']}](/reading/digest/weekly/{w['id']}/)")
            lines.append("")
            if w.get("theme"):
                lines.append(f"- {w['theme']}")
            lines.append(f"- 论文 {w.get('paper_count', 0)} 篇")
            lines.append("")

    if prep["daily_reports"]:
        lines.append("## 每日索引")
        lines.append("")
        for d in sorted(prep["daily_reports"], key=lambda x: x.get("id", "")):
            theme = d.get("theme", "无主题")
            lines.append(f"- [{d['id']}](/reading/digest/daily/{d['id']}/) — {theme} ({d.get('paper_count', 0)} 篇)")
        lines.append("")

    out_dir = _digest_dir("monthly")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{month}.md"
    json_path = out_dir / f"{month}.json"
    write_text(md_path, "\n".join(lines))
    write_json(json_path, meta.to_dict())
    return md_path


# ── Loading ──────────────────────────────────────────────────


def load_digests(period_type: str | None = None) -> list[DigestMeta]:
    """Load all digests, optionally filter by period type."""
    results = []
    base = DIGESTS_DIR / "2026"
    if not base.exists():
        return results
    types = [period_type] if period_type else ["daily", "weekly", "monthly"]
    for pt in types:
        d = base / pt
        if not d.exists():
            continue
        for jf in sorted(d.glob("*.json"), reverse=True):
            try:
                m = read_json(jf)
                results.append(DigestMeta.from_dict(m))
            except Exception:
                continue
    return results


def load_digest(period_type: str, digest_id: str) -> tuple[DigestMeta | None, str]:
    """Load a single digest's meta + markdown body."""
    d = DIGESTS_DIR / "2026" / period_type
    json_file = d / f"{digest_id}.json"
    md_file = d / f"{digest_id}.md"
    if not json_file.exists() or not md_file.exists():
        return None, ""
    meta = DigestMeta.from_dict(read_json(json_file))
    body = md_file.read_text(encoding="utf-8")
    return meta, body
