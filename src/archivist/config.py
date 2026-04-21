"""Configuration and path constants.

Resolution rules
----------------
1. Project root:  ``$ARCHIVIST_ROOT`` env var if set, otherwise auto-detected
   from this file's location (``parents[2]``).
2. Config files:  ``<project_root>/config.yaml`` (framework defaults,
   tracked in git) merged with ``<project_root>/config.local.yaml``
   (user-specific values, gitignored). The local file deep-overrides the
   base — missing file is fine.
3. Archive dir:   ``project.archive_dir`` from merged config (relative to
   project root unless absolute), default ``archive``.

User-specific values (deploy host, site base_url, lark user id) belong
in ``config.local.yaml``; see ``config.local.yaml.example`` for the
expected shape. Everything else (ArXiv keywords, company list, tags)
stays in the shared ``config.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _detect_project_root() -> Path:
    env = os.environ.get("ARCHIVIST_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # src/archivist/config.py → parents: [archivist, src, project_root]
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT: Path = _detect_project_root()
CONFIG_FILE: Path = PROJECT_ROOT / "config.yaml"
CONFIG_LOCAL_FILE: Path = PROJECT_ROOT / "config.local.yaml"


_CONFIG_CACHE: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. override wins on leaf keys."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config() -> dict[str, Any]:
    """Load and cache config.yaml merged with config.local.yaml (if present)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    base = _read_yaml(CONFIG_FILE)
    local = _read_yaml(CONFIG_LOCAL_FILE)
    _CONFIG_CACHE = _deep_merge(base, local) if local else base
    return _CONFIG_CACHE


def _resolve_archive_root() -> Path:
    cfg = load_config().get("project", {}) or {}
    raw = cfg.get("archive_dir", "archive")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


ARCHIVE_ROOT: Path = _resolve_archive_root()

PAPERS_DIR = ARCHIVE_ROOT / "papers"
PAPERS_BRIEF_DIR = ARCHIVE_ROOT / "papers_brief"
DOCS_DIR = ARCHIVE_ROOT / "docs"
DIGESTS_DIR = ARCHIVE_ROOT / "digests"
BENCHMARKS_DIR = ARCHIVE_ROOT / "benchmarks"
MODEL_GRAPH_DIR = ARCHIVE_ROOT / "model-graph"


def ensure_archive_dirs() -> None:
    """Create all archive directories if they don't exist."""
    for d in [PAPERS_DIR, PAPERS_BRIEF_DIR, DOCS_DIR, DIGESTS_DIR, BENCHMARKS_DIR, MODEL_GRAPH_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# ── Typed accessors for user-facing config sections ──────────


def get_site_base_url() -> str:
    return (load_config().get("site", {}) or {}).get("base_url", "")


def get_deploy_settings() -> dict[str, str]:
    cfg = load_config().get("deploy", {}) or {}
    return {
        "host": cfg.get("host", ""),
        "remote_site_path": cfg.get("remote_site_path", "~/site"),
        "remote_archive_path": cfg.get("remote_archive_path", "~/archive"),
    }


def get_lark_user_id() -> str:
    return (load_config().get("lark", {}) or {}).get("notify_user_id", "")
