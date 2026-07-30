"""Arxiv API client for fetching recent papers."""

import http.client
import random
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

MAX_RETRIES = 4
# Escalating 429 backoff (seconds), indexed by attempt + jitter. A short first
# wait recovers from brief rate-limit windows in ~1min instead of stalling 10min;
# later waits grow to outlast sustained blocks. Worst case ≈ 18min over 4 tries
# (was a flat 600s × 4 = 40min, which disguised rate-limiting as a frozen hang).
RATELIMIT_BACKOFF = (60, 150, 300, 600)
NETWORK_BACKOFF = [5, 15, 30, 60]  # transient network / truncated-read errors — retry quickly

# An empty ProxyHandler bypasses environment and system proxies for metadata
# fetches and PDF downloads, avoiding failures from an unavailable proxy.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _urlopen_with_retry(req, timeout=60):
    """urlopen + read with retry, bypassing any configured proxy.

    - 429 / 5xx (rate-limit / server): long fixed backoff to outlast the window.
    - network / truncated-read errors (URLError, timeout, OSError, and
      ``http.client.HTTPException`` such as ``IncompleteRead``): short escalating
      backoff — these are usually transient one-offs, not rate limits.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _NO_PROXY_OPENER.open(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < MAX_RETRIES:
                base = RATELIMIT_BACKOFF[min(attempt, len(RATELIMIT_BACKOFF) - 1)]
                wait = base + random.uniform(0, base * 0.2)  # jitter: avoid synchronized retries
                print(f"  ArXiv API {e.code}, retry {attempt+1}/{MAX_RETRIES} in {wait:.0f}s...", flush=True)
                time.sleep(wait)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            if attempt < MAX_RETRIES:
                wait = NETWORK_BACKOFF[min(attempt, len(NETWORK_BACKOFF) - 1)]
                print(f"  ArXiv network error ({type(e).__name__}: {e}), retry {attempt+1}/{MAX_RETRIES} in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                raise


@dataclass
class RawPaper:
    arxiv_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    categories: list[str] = field(default_factory=list)
    published: str = ""
    updated: str = ""
    pdf_url: str = ""


def fetch_category(
    category: str,
    max_results: int = 100,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    start_index: int = 0,
) -> list[RawPaper]:
    """Fetch recent papers from a single Arxiv category.

    Args:
        date: Single date (YYYY-MM-DD) - fetch papers submitted on that date.
        date_from/date_to: Date range (YYYY-MM-DD) - fetch papers in range.
        If neither given, fetch most recent papers.
    """
    if date_from and date_to:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to = datetime.strptime(date_to, "%Y-%m-%d")
        start = dt_from.strftime("%Y%m%d0000")
        end = (dt_to + timedelta(days=1)).strftime("%Y%m%d0000")
        query = f"cat:{category} AND submittedDate:[{start} TO {end}]"
    elif date:
        dt = datetime.strptime(date, "%Y-%m-%d")
        start = dt.strftime("%Y%m%d0000")
        end = (dt + timedelta(days=1)).strftime("%Y%m%d0000")
        query = f"cat:{category} AND submittedDate:[{start} TO {end}]"
    else:
        query = f"cat:{category}"

    params = urllib.parse.urlencode({
        "search_query": query,
        "start": start_index,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })

    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "archivist/0.1"})

    xml_data = _urlopen_with_retry(req, timeout=60)

    return _parse_feed(xml_data)


def fetch_category_all(
    category: str,
    date_from: str,
    date_to: str,
    max_per_page: int = 200,
    max_total: int = 2000,
) -> list[RawPaper]:
    """Fetch ALL papers in a date range with pagination."""
    all_papers = []
    start = 0
    while start < max_total:
        batch = fetch_category(
            category,
            max_results=max_per_page,
            date_from=date_from,
            date_to=date_to,
            start_index=start,
        )
        if not batch:
            break
        all_papers.extend(batch)
        if len(batch) < max_per_page:
            break  # No more results
        start += max_per_page
        time.sleep(3)  # Respect API rate limits
    else:
        # Ran the range dry only if we broke out above; falling through the
        # while condition means max_total cut us off. Never let that be silent.
        print(
            f"  ⚠ {category} {date_from}..{date_to}: hit max_total={max_total}, "
            f"results TRUNCATED — raise max_total or narrow the range",
            flush=True,
        )
    return all_papers


def fetch_papers(
    categories: list[str],
    max_results: int = 100,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[RawPaper]:
    """Fetch papers from multiple categories, deduplicated by arxiv_id.

    Every date-filtered path paginates until the day/range is exhausted, so
    ``max_results`` is a *page size*, not a ceiling on what a day can yield.
    A single-page ceiling with ``sortOrder=descending`` would drop the day's
    earliest submissions before keyword filtering whenever the category count
    exceeded the page size. Pagination preserves the complete candidate set.
    """
    seen = set()
    papers = []

    for cat in categories:
        if date_from and date_to:
            cat_papers = fetch_category_all(cat, date_from=date_from, date_to=date_to, max_per_page=max_results)
        elif date:
            # Single day == a one-day range; fetch_category builds an identical
            # query for both, so this only adds the missing pagination.
            cat_papers = fetch_category_all(cat, date_from=date, date_to=date, max_per_page=max_results)
        else:
            # No date filter: "the most recent N" — here the cap is the intent.
            cat_papers = fetch_category(cat, max_results=max_results)
        for p in cat_papers:
            if p.arxiv_id not in seen:
                seen.add(p.arxiv_id)
                papers.append(p)
        # Be polite to the API
        if len(categories) > 1:
            time.sleep(3)

    return papers


def _parse_feed(xml_data: bytes) -> list[RawPaper]:
    """Parse Arxiv Atom feed XML into RawPaper objects."""
    root = ET.fromstring(xml_data)
    papers = []

    for entry in root.findall(f"{ATOM_NS}entry"):
        # Extract arxiv_id from the id URL
        id_text = entry.findtext(f"{ATOM_NS}id", "")
        arxiv_id = id_text.split("/abs/")[-1] if "/abs/" in id_text else id_text

        title = entry.findtext(f"{ATOM_NS}title", "").strip()
        # Clean up whitespace in title
        title = " ".join(title.split())

        abstract = entry.findtext(f"{ATOM_NS}summary", "").strip()
        abstract = " ".join(abstract.split())

        published = entry.findtext(f"{ATOM_NS}published", "")
        updated = entry.findtext(f"{ATOM_NS}updated", "")

        # Authors and affiliations
        authors = []
        affiliations = []
        for author_elem in entry.findall(f"{ATOM_NS}author"):
            name = author_elem.findtext(f"{ATOM_NS}name", "")
            if name:
                authors.append(name)
            # Arxiv affiliations (if present)
            for aff in author_elem.findall(f"{ARXIV_NS}affiliation"):
                if aff.text:
                    affiliations.append(aff.text)

        # Categories
        categories = []
        for cat_elem in entry.findall(f"{ATOM_NS}category"):
            term = cat_elem.get("term", "")
            if term:
                categories.append(term)

        # PDF link
        pdf_url = ""
        for link_elem in entry.findall(f"{ATOM_NS}link"):
            if link_elem.get("title") == "pdf":
                pdf_url = link_elem.get("href", "")
                break

        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

        papers.append(RawPaper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            affiliations=affiliations,
            abstract=abstract,
            categories=categories,
            published=published,
            updated=updated,
            pdf_url=pdf_url,
        ))

    return papers


def fetch_by_id(arxiv_id: str) -> RawPaper | None:
    """Fetch a single paper by arxiv_id via the API's id_list parameter."""
    params = urllib.parse.urlencode({"id_list": arxiv_id, "max_results": 1})
    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "archivist/0.1"})
    xml_data = _urlopen_with_retry(req, timeout=60)
    results = _parse_feed(xml_data)
    return results[0] if results else None


def search_by_title(title: str, max_results: int = 5) -> list[RawPaper]:
    """Search Arxiv papers by title and return matching results."""
    query = f'ti:"{title}"'
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "archivist/0.1"})
    xml_data = _urlopen_with_retry(req, timeout=60)
    return _parse_feed(xml_data)


def download_pdf(url: str, dest: str) -> None:
    """Download a PDF from URL to local path."""
    req = urllib.request.Request(url, headers={"User-Agent": "archivist/0.1"})
    data = _urlopen_with_retry(req, timeout=120)
    with open(dest, "wb") as f:
        f.write(data)
