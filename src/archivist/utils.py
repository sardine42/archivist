"""Utility functions: slug generation, JSON I/O, ID generation."""

import json
import re
import uuid
from pathlib import Path


def generate_id() -> str:
    """Generate a short unique ID."""
    return uuid.uuid4().hex[:12]


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a filesystem-safe slug.

    Keeps ASCII alphanumerics and hyphens. Chinese characters are kept as-is.
    """
    # Replace whitespace and special chars with hyphens
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text.strip())
    text = re.sub(r"-+", "-", text).strip("-").lower()
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "untitled"


def read_json(path: Path) -> dict:
    """Read a JSON file and return its contents."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    """Write data to a JSON file with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_text(path: Path) -> str:
    """Read a text file."""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Write content to a text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
