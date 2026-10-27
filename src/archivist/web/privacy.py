"""Keep personal annotations outside publicly rendered paper metadata."""

from dataclasses import replace

from archivist.models import PaperMeta


def public_paper(paper: PaperMeta) -> PaperMeta:
    return replace(paper, rating=None, rating_reason="", notes="", feedback_consumed=False)
