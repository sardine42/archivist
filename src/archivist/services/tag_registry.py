"""Tag whitelist source-of-truth and validation.

config.yaml `tags:` is a flat list[str] of allowed tag names. This module
loads it once per process and validates tag input at all write boundaries
(CLI, apply-reading, LLM auto-tag).
"""

from functools import lru_cache

from archivist.config import load_config


@lru_cache(maxsize=1)
def load_whitelist() -> frozenset[str]:
    """Return the canonical tag whitelist as a frozen set."""
    config = load_config()
    raw = config.get("tags", [])
    if isinstance(raw, dict):
        # Legacy dict-of-lists shape — flatten for backward compatibility
        flat = []
        for vals in raw.values():
            if isinstance(vals, list):
                flat.extend(vals)
        return frozenset(t for t in flat if isinstance(t, str))
    if isinstance(raw, list):
        return frozenset(t for t in raw if isinstance(t, str))
    return frozenset()


def reload_whitelist() -> None:
    """Drop the cache after `archivist tag promote` mutates config.yaml.

    Also clears the underlying config.yaml cache, otherwise the next
    `load_whitelist()` call would still see the pre-edit config.
    """
    from archivist.config import reload_config
    reload_config()
    load_whitelist.cache_clear()


def validate_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Split a tag list into (valid, unknown). Preserves input order, dedups."""
    whitelist = load_whitelist()
    seen: set[str] = set()
    valid: list[str] = []
    unknown: list[str] = []
    for t in tags:
        t = (t or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        if t in whitelist:
            valid.append(t)
        else:
            unknown.append(t)
    return valid, unknown


def suggest_similar(unknown: str, limit: int = 3) -> list[str]:
    """Return up to `limit` whitelist tags closest to `unknown` (substring + difflib)."""
    import difflib

    whitelist = load_whitelist()
    if not whitelist:
        return []
    needle = unknown.lower()
    substr = sorted(t for t in whitelist if needle in t.lower() or t.lower() in needle)
    if substr[:limit]:
        return substr[:limit]
    return difflib.get_close_matches(unknown, list(whitelist), n=limit, cutoff=0.5)
