"""End-to-end script orchestrator for daily / weekly / monthly digests.

This is the script-driven replacement for the agent-driven Skill flow.
The orchestrator runs deterministic steps directly and only delegates to
``claude -p`` (via ``claude_runner``) for the three judgment-required
points: paper scoring, paper deep-reading, and digest summarization.

Public entry points:

    run_daily(date_str)      # full daily pipeline (fetch → score → read → digest → push → deploy)
    run_weekly(week_str)     # weekly aggregation only (no fetch/read)
    run_monthly(month_str)   # monthly aggregation only (no fetch/read)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Any

from archivist.config import (
    ARCHIVE_ROOT,
    PAPERS_BRIEF_DIR,
    PAPERS_DIR,
    get_deploy_settings,
    get_lark_user_id,
    load_config,
)

# Project root (parent of the archive data dir)
BASE_DIR = ARCHIVE_ROOT.parent
from archivist.services.arxiv_scorer import (
    archive_scored_paper,
    pre_filter,
)
from archivist.services.arxiv_fetch import fetch_papers
from archivist.services.claude_runner import ClaudeRunnerError, run_claude_json, run_claude
from archivist.services.digest import (
    _coverage_range,
    prepare_daily,
    prepare_monthly,
    prepare_weekly,
    write_daily,
    write_monthly,
    write_weekly,
)
from archivist.services.lark_push import LarkPushError, push_digest_to_lark


PROMPTS_DIR = Path(__file__).parent / "digest_prompts"
SCORING_CRITERIA = BASE_DIR / "archive" / "criteria" / "scoring-criteria.md"


# ── Logging ───────────────────────────────────────────────────────


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("digest_runner")
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


log = _setup_logger()


def _step(name: str):
    """Decorator-ish helper: log start/end + elapsed for a callable."""
    def deco(fn):
        def wrapper(*args, **kwargs):
            log.info(f"━━ {name} start")
            t0 = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                log.info(f"━━ {name} done in {time.monotonic() - t0:.1f}s")
                return result
            except Exception as e:
                log.error(f"━━ {name} FAILED after {time.monotonic() - t0:.1f}s: {e}")
                raise
        return wrapper
    return deco


# ── Utilities ─────────────────────────────────────────────────────


def _today_iso() -> str:
    return date_type.today().isoformat()


def _iter_dates(start_iso: str, end_iso: str):
    s = date_type.fromisoformat(start_iso)
    e = date_type.fromisoformat(end_iso)
    cur = s
    while cur <= e:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _strip_v(arxiv_id: str) -> str:
    if "v" in arxiv_id:
        head, tail = arxiv_id.rsplit("v", 1)
        if tail.isdigit():
            return head
    return arxiv_id


# ── Step 1: fetch ─────────────────────────────────────────────────


@_step("Step 1 — fetch candidates")
def _fetch_candidates(coverage_start: str, coverage_end: str) -> list[dict]:
    """Fetch and pre-filter candidates for each day in the coverage range.

    Returns a deduped flat list of candidate dicts (one entry per arxiv_id).
    Side-effect free aside from arxiv API calls.
    """
    config = load_config()
    arxiv_cfg = config.get("arxiv", {})
    cat_list = arxiv_cfg.get("categories", ["cs.IR", "cs.LG"])
    max_results = arxiv_cfg.get("max_results_per_category", 100)

    seen: dict[str, dict] = {}  # clean_id → candidate dict + CandidatePaper
    candidate_objs = []  # parallel list of CandidatePaper

    for day in _iter_dates(coverage_start, coverage_end):
        log.info(f"  fetching {day}…")
        raw = fetch_papers(cat_list, max_results=max_results, date=day)
        log.info(f"  {day}: {len(raw)} fetched")
        if not raw:
            continue
        candidates = pre_filter(raw)
        log.info(f"  {day}: {len(candidates)} passed keyword filter")
        for c in candidates:
            clean = _strip_v(c.paper.arxiv_id)
            if clean in seen:
                continue
            seen[clean] = {"candidate": c}
            candidate_objs.append(c)

    log.info(f"  total unique candidates: {len(candidate_objs)}")
    return candidate_objs


# ── Step 2: score via claude -p ──────────────────────────────────


def _build_score_prompt(candidate_dicts: list[dict]) -> str:
    template = (PROMPTS_DIR / "score_papers.md").read_text(encoding="utf-8")
    criteria = SCORING_CRITERIA.read_text(encoding="utf-8")
    return template.format(
        scoring_criteria=criteria,
        candidates_json=json.dumps(candidate_dicts, ensure_ascii=False, indent=2),
        n_candidates=len(candidate_dicts),
    )


@_step("Step 2 — score + summarize via claude -p")
def _score_candidates(candidates: list) -> list[dict]:
    """Call claude -p once to score all candidates. Returns score result list."""
    if not candidates:
        log.info("  no candidates → skipping scoring")
        return []

    from archivist.services.arxiv_scorer import candidates_to_json
    cand_dicts = candidates_to_json(candidates)
    prompt = _build_score_prompt(cand_dicts)

    log.info(f"  invoking claude -p on {len(cand_dicts)} candidates…")
    result = run_claude_json(prompt, retries=1)
    if not isinstance(result, list):
        raise ClaudeRunnerError(
            f"score result must be a JSON array, got {type(result).__name__}"
        )

    by_id = {r.get("arxiv_id"): r for r in result if r.get("arxiv_id")}
    log.info(f"  received {len(result)} score entries")

    # Persist meta.json under papers_brief/
    written = 0
    for c in candidates:
        score_result = by_id.get(c.paper.arxiv_id) or by_id.get(_strip_v(c.paper.arxiv_id))
        if not score_result:
            log.warning(f"  no score for {c.paper.arxiv_id} — skipped")
            continue
        path = archive_scored_paper(c, score_result)
        if path:
            written += 1
    log.info(f"  wrote {written} brief metas to papers_brief/")
    return [by_id.get(c.paper.arxiv_id, {}) for c in candidates]


# ── Step 3: deep read top-k via claude -p /read-paper ─────────────


@_step("Step 3 — deep read top-k papers via claude -p /read-paper")
def _deep_read_top_k(candidates: list, score_results: list[dict]) -> list[str]:
    """Pick papers with score >= threshold, top_k_deep_read, run /read-paper serial."""
    config = load_config().get("arxiv", {})
    threshold = config.get("deep_read_threshold", 7)
    top_k = config.get("top_k_deep_read", 6)

    paired = []
    for c, r in zip(candidates, score_results):
        if not r:
            continue
        if c.is_existing:
            continue  # already deeply archived; do not redo
        try:
            score = float(r.get("score", 0))
        except (TypeError, ValueError):
            continue
        if score < threshold:
            continue
        paired.append((score, c, r))
    paired.sort(key=lambda x: -x[0])
    selected = paired[:top_k]

    if not selected:
        log.info(f"  no papers ≥ score {threshold} → skipping deep read")
        return []

    log.info(f"  deep reading {len(selected)} paper(s) (threshold={threshold}, top_k={top_k}):")
    for s, c, _ in selected:
        log.info(f"    score={s:.0f} {c.paper.arxiv_id} | {c.paper.title[:70]}")

    succeeded = []
    for score, c, _ in selected:
        arxiv_id = _strip_v(c.paper.arxiv_id)
        title = c.paper.title.replace('"', '\\"')
        prompt = f'/read-paper {arxiv_id} --title "{title}"'
        try:
            log.info(f"  → /read-paper {arxiv_id}")
            run_claude(prompt, timeout=3600)
            succeeded.append(arxiv_id)
        except Exception as e:
            log.error(f"  /read-paper {arxiv_id} FAILED: {e}")
            # Continue with the next paper instead of aborting the whole digest
    log.info(f"  deep read summary: {len(succeeded)}/{len(selected)} succeeded")
    return succeeded


# ── Step 4: theme generation via claude -p ───────────────────────


@_step("Step 4 — generate digest theme via claude -p")
def _generate_theme(period: str, period_id: str, prepare_data: dict) -> dict:
    """Call claude -p to produce {theme, theme_tags, highlights, summary}."""
    template = (PROMPTS_DIR / f"{period}_theme.md").read_text(encoding="utf-8")
    key = {"daily": "date", "weekly": "week", "monthly": "month"}[period]
    prompt = template.format(
        **{key: period_id},
        prepare_json=json.dumps(prepare_data, ensure_ascii=False, indent=2),
    )

    log.info(f"  invoking claude -p for {period} theme…")
    result = run_claude_json(prompt, retries=1)
    if not isinstance(result, dict):
        raise ClaudeRunnerError(
            f"theme result must be a JSON object, got {type(result).__name__}"
        )
    # Ensure required keys present (defaults for empty days)
    return {
        "theme": result.get("theme", ""),
        "theme_tags": result.get("theme_tags") or [],
        "highlights": result.get("highlights") or [],
        "summary": result.get("summary", ""),
    }


# ── Step 5/6: push + deploy ──────────────────────────────────────


@_step("Step 5 — push to Lark + pin")
def _push(period: str, period_id: str, paper_count: int) -> None:
    if paper_count == 0:
        log.info("  empty digest — skipping Lark push to avoid noise")
        return
    if not get_lark_user_id():
        log.info("  lark.notify_user_id not configured — skipping push")
        return
    msg_id = push_digest_to_lark(period, period_id)
    log.info(f"  sent + pinned: {msg_id}")


@_step("Step 6 — build + deploy")
def _deploy() -> None:
    if not get_deploy_settings()["host"]:
        log.info("  deploy.host not configured — skipping build + deploy")
        return
    venv_archivist = BASE_DIR / ".venv" / "bin" / "archivist"
    proc = subprocess.run(
        [str(venv_archivist), "deploy"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"archivist deploy failed: {proc.stderr[:500]}")
    # echo last 5 lines so the cron log shows what was deployed
    for ln in proc.stdout.strip().splitlines()[-5:]:
        log.info(f"  {ln}")


# ── Public entry points ──────────────────────────────────────────


def run_daily(date_str: str | None = None) -> None:
    date = date_str or _today_iso()
    log.info(f"╔══ daily digest run for {date} ══╗")

    coverage_start, coverage_end = _coverage_range(date)
    log.info(f"  coverage range: {coverage_start} ~ {coverage_end}")

    candidates = _fetch_candidates(coverage_start, coverage_end)
    score_results = _score_candidates(candidates)
    _deep_read_top_k(candidates, score_results)

    prepare_data = prepare_daily(date)
    paper_count = prepare_data["stats"]["total"]
    log.info(f"  prepared {paper_count} papers for the digest")

    # Short-circuit for empty days: don't generate theme, don't write a stub
    # digest, don't push, don't redeploy. Keeps the report list clean.
    if paper_count == 0:
        log.info("  empty day — skipping theme / write / push / deploy")
        log.info(f"╚══ daily digest skipped (empty) for {date} ══╝")
        return

    theme = _generate_theme("daily", date, prepare_data)
    write_daily(date, theme)

    _push("daily", date, paper_count)
    _deploy()
    log.info(f"╚══ daily digest done for {date} ══╝")


def run_weekly(week_str: str | None = None) -> None:
    if not week_str:
        iso = date_type.today().isocalendar()
        week_str = f"{iso[0]}-W{iso[1]:02d}"
    log.info(f"╔══ weekly digest run for {week_str} ══╗")

    prepare_data = prepare_weekly(week_str)
    daily_count = len(prepare_data.get("daily_reports", []))
    log.info(f"  aggregating {daily_count} daily reports")

    if daily_count == 0:
        log.info("  empty week — skipping theme / write / push / deploy")
        log.info(f"╚══ weekly digest skipped (empty) for {week_str} ══╝")
        return

    theme = _generate_theme("weekly", week_str, prepare_data)
    write_weekly(week_str, theme)

    _push("weekly", week_str, daily_count)
    _deploy()
    log.info(f"╚══ weekly digest done for {week_str} ══╝")


def run_monthly(month_str: str | None = None) -> None:
    if not month_str:
        month_str = date_type.today().strftime("%Y-%m")
    log.info(f"╔══ monthly digest run for {month_str} ══╗")

    prepare_data = prepare_monthly(month_str)
    daily_count = len(prepare_data.get("daily_reports", []))
    weekly_count = len(prepare_data.get("weekly_reports", []))
    log.info(f"  aggregating {daily_count} daily + {weekly_count} weekly reports")

    if daily_count == 0 and weekly_count == 0:
        log.info("  empty month — skipping theme / write / push / deploy")
        log.info(f"╚══ monthly digest skipped (empty) for {month_str} ══╝")
        return

    theme = _generate_theme("monthly", month_str, prepare_data)
    write_monthly(month_str, theme)

    total_for_push = daily_count + weekly_count
    _push("monthly", month_str, total_for_push)
    _deploy()
    log.info(f"╚══ monthly digest done for {month_str} ══╝")
