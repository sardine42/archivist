"""Feedback collection helpers for the refine-rubric skill."""

from dataclasses import dataclass

from archivist.models import PaperMeta
from archivist.services import paper_store


@dataclass
class PendingCorrection:
    slug: str
    title: str
    arxiv_id: str | None
    deeply_read: bool
    auto_score: float
    rating: int
    rating_reason: str
    deviation: float

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "arxiv_id": self.arxiv_id,
            "deeply_read": self.deeply_read,
            "auto_score": self.auto_score,
            "rating": self.rating,
            "rating_reason": self.rating_reason,
            "deviation": self.deviation,
        }


def auto_score_of(paper: PaperMeta) -> float:
    """Return reading_score for deeply-read papers, else score."""
    if paper.deeply_read:
        return float(paper.reading_score or 0.0)
    return float(paper.score or 0.0)


def collect_corrections() -> list[PendingCorrection]:
    """Return unprocessed rating corrections, largest deviation first.

    Filter: rating set, feedback_consumed False, rating != auto_score.
    Any non-zero deviation qualifies — every disagreement should surface in
    an agent session for user confirmation.
    """
    out: list[PendingCorrection] = []
    for paper in paper_store.list_papers():
        if paper.rating is None or paper.feedback_consumed:
            continue
        auto = auto_score_of(paper)
        if float(paper.rating) == auto:
            continue
        out.append(PendingCorrection(
            slug=paper.slug,
            title=paper.title,
            arxiv_id=paper.arxiv_id,
            deeply_read=paper.deeply_read,
            auto_score=auto,
            rating=int(paper.rating),
            rating_reason=paper.rating_reason,
            deviation=float(paper.rating) - auto,
        ))
    out.sort(key=lambda c: abs(c.deviation), reverse=True)
    return out
