#!/usr/bin/env python3
"""Re-tag deeply-read papers under the new flat tag taxonomy.

Only processes papers with a `reading.md` file (i.e. those that went
through the deep-read pipeline). For each, sends Claude the full reading
report — much richer than the abstract — and asks for a fresh tag list
drawn strictly from config.yaml's flat whitelist, plus optional
`proposed_tags` for genuinely missing taxonomy entries.

Brief papers (abstract-only entries under archive/papers_brief/) are
intentionally skipped — they're not shown on the website and re-tagging
them adds cost without value.

Usage:
  python scripts/retag-papers.py --dry-run                  # diff only
  python scripts/retag-papers.py --dry-run --limit 10       # first 10 papers
  python scripts/retag-papers.py --apply                    # apply via CLI
  python scripts/retag-papers.py --apply --slug <slug>      # one paper
  python scripts/retag-papers.py --apply --filter-score 7   # only score≥7

Each apply shells out to `archivist paper edit`, which validates against
the whitelist (CLAUDE.md: agents must write through the CLI, not by
direct meta.json edits).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from archivist.config import PAPERS_DIR
from archivist.services.claude_runner import ClaudeRunnerError, run_claude_json
from archivist.services.tag_registry import load_whitelist


PROMPT_TEMPLATE = """\
You are re-tagging a research paper under a new flat tag taxonomy. Output STRICT JSON only.

# Whitelist (use ONLY these for `tags`)
{whitelist_table}

# Rules
1. `tags`: 4–6 entries chosen ONLY from the whitelist above. Pick the smallest
   set that captures the paper's substance — do not pad. If genuinely
   uncertain, fewer tags is better than wrong tags.
2. `proposed_tags`: 0–2 entries IF (and only if) the paper has a clear,
   reusable theme that the whitelist truly fails to express. Each entry must
   be a category-level concept (≤3 hyphenated English words like
   `mixture-of-depths`), NOT a paper-specific innovation (those go in
   keywords). Almost always leave empty `[]`.
3. The `category` field is already set; do not duplicate category info in
   tags. The legacy task tags `ctr-prediction` / `sequential-rec` /
   `generative-retrieval` are PROHIBITED — they duplicate `category`.
4. Output ONLY a JSON object. No prose, no markdown fence.

Schema:
{{
  "tags": ["transformer", "moe", "industrial"],
  "proposed_tags": []
}}

# Paper

Title: {title}
Category: {category}
Affiliations: {affiliations}
Current tags (may include obsolete ones): {old_tags}
Current keywords (paper-specific concepts; may inform tag choice): {old_keywords}

Deep-read report (authoritative source for tagging — use this over your prior knowledge):
{reading_md}
"""


WHITELIST_GLOSS = {
    "transformer": "Transformer / 注意力骨干",
    "moe": "Mixture-of-Experts 路由",
    "diffusion": "扩散模型",
    "pretrained-lm": "用预训练 LLM 作组件 (≠ category=llm)",
    "rl": "强化学习训练",
    "contrastive-ssl": "对比学习 / 自监督",
    "knowledge-distillation": "知识蒸馏",
    "process-supervision": "监督中间步骤 (深度监督 / PRM 风格)",
    "parameter-scaling": "扩参 / scaling law",
    "recursive-depth": "权重共享深度方向递归 (Universal Transformer / ALBERT / LoopCTR)",
    "semantic-id": "离散 semantic token 化物品",
    "feature-interaction": "显式特征交叉 (DCN / DeepFM)",
    "quantization": "量化模型 / 特征",
    "cold-start": "冷启动场景",
    "search-ranking": "搜索排序专题",
    "ad-rec": "广告推荐",
    "industrial": "来自有线上系统的公司",
    "academic": "仅学术 / 无部署",
}


def _build_whitelist_table() -> str:
    whitelist = load_whitelist()
    lines = []
    for tag in sorted(whitelist):
        gloss = WHITELIST_GLOSS.get(tag, "")
        lines.append(f"- `{tag}` — {gloss}" if gloss else f"- `{tag}`")
    return "\n".join(lines)


def _deeply_read_paper_dirs() -> list[Path]:
    """Yield paper dirs that have a reading.md (i.e. went through deep-read)."""
    if not PAPERS_DIR.exists():
        return []
    out = []
    for meta_file in PAPERS_DIR.rglob("meta.json"):
        if (meta_file.parent / "reading.md").exists():
            out.append(meta_file.parent)
    return out


def _retag_one(meta: dict, reading_md: str, whitelist_table: str) -> dict:
    """Call Claude and return {"tags": [...], "proposed_tags": [...]}."""
    # Cap reading_md to keep prompt size bounded; deep-read reports are 3-15K
    # tokens typically. 12000 chars ≈ 3-4K tokens, plenty of signal.
    prompt = PROMPT_TEMPLATE.format(
        whitelist_table=whitelist_table,
        title=meta.get("title", ""),
        category=", ".join(meta.get("category", []) or []),
        affiliations=", ".join((meta.get("affiliations") or [])[:5]),
        old_tags=", ".join(meta.get("tags", []) or []) or "(none)",
        old_keywords=", ".join((meta.get("keywords") or [])[:10]) or "(none)",
        reading_md=reading_md[:12000],
    )
    result = run_claude_json(prompt, model="opus", retries=2, timeout=300)
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object, got {type(result).__name__}")
    return {
        "tags": list(result.get("tags") or []),
        "proposed_tags": list(result.get("proposed_tags") or []),
    }


def _apply_via_cli(slug: str, tags: list[str], proposed_tags: list[str]) -> None:
    """Shell out to `archivist paper edit` so whitelist validation runs."""
    cmd = [
        sys.executable, "-m", "archivist.cli",
        "paper", "edit", slug,
        "--tags", ",".join(tags),
        "--proposed-tags", ",".join(proposed_tags),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"archivist paper edit failed for {slug}:\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


def _format_diff(slug: str, old: list[str], new_tags: list[str], new_proposed: list[str]) -> str:
    old_set = set(old)
    new_set = set(new_tags)
    removed = sorted(old_set - new_set)
    added = sorted(new_set - old_set)
    kept = sorted(old_set & new_set)
    lines = [f"  {slug}"]
    lines.append(f"    old: {', '.join(old) or '(none)'}")
    lines.append(f"    new: {', '.join(new_tags) or '(none)'}")
    if added:
        lines.append(f"    + {', '.join(added)}")
    if removed:
        lines.append(f"    - {', '.join(removed)}")
    if new_proposed:
        lines.append(f"    proposed: {', '.join(new_proposed)}")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Print diff, do not write")
    p.add_argument("--apply", action="store_true", help="Write changes via archivist CLI")
    p.add_argument("--slug", help="Limit to a single paper slug")
    p.add_argument("--filter-score", type=float, default=0.0,
                   help="Only re-tag papers with score >= N (default 0)")
    p.add_argument("--limit", type=int, default=0, help="Max papers to process (0 = all)")
    args = p.parse_args()

    if not (args.dry_run or args.apply):
        p.error("Specify --dry-run or --apply")

    whitelist_table = _build_whitelist_table()
    paper_dirs = _deeply_read_paper_dirs()
    print(f"Found {len(paper_dirs)} deeply-read papers (with reading.md)", file=sys.stderr)

    processed = 0
    skipped = 0
    for paper_dir in paper_dirs:
        meta = json.loads((paper_dir / "meta.json").read_text(encoding="utf-8"))
        slug = meta.get("slug") or paper_dir.name

        if args.slug and slug != args.slug:
            continue
        if float(meta.get("score") or 0) < args.filter_score:
            skipped += 1
            continue
        if args.limit and processed >= args.limit:
            break

        reading_md = (paper_dir / "reading.md").read_text(encoding="utf-8")
        old_tags = list(meta.get("tags") or [])
        try:
            result = _retag_one(meta, reading_md, whitelist_table)
        except (ClaudeRunnerError, ValueError) as e:
            print(f"  {slug}: ERROR {e}", file=sys.stderr)
            continue

        new_tags = result["tags"]
        new_proposed = result["proposed_tags"]

        print(_format_diff(slug, old_tags, new_tags, new_proposed))

        if args.apply:
            try:
                _apply_via_cli(slug, new_tags, new_proposed)
            except RuntimeError as e:
                print(f"    APPLY FAILED: {e}", file=sys.stderr)
                continue

        processed += 1

    print(f"\nProcessed: {processed}  Skipped (score filter): {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
