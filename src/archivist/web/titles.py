"""Shared paper-title presentation for the live and static website."""

import re

from markupsafe import Markup, escape


def format_paper_title(title: str, model_name: str | None = None) -> Markup:
    """Highlight an existing model name, or prepend it when absent."""
    model_name = (model_name or "").strip()
    if not model_name:
        return escape(title)

    # Match names literally and preserve their spelling in the title. Boundaries
    # keep names such as LIMIT and COUNT out of Limitations and Counting.
    pattern = re.compile(r"(?<!\w)" + re.escape(model_name) + r"(?!\w)", re.IGNORECASE)
    model_span = Markup('<span class="paper-title-model">{}</span>')
    parts = []
    cursor = 0
    for match in pattern.finditer(title):
        parts.extend((escape(title[cursor:match.start()]), model_span.format(match.group())))
        cursor = match.end()
    if not parts:
        return model_span.format(model_name) + ": " + escape(title)
    parts.append(escape(title[cursor:]))
    return Markup("").join(parts)
