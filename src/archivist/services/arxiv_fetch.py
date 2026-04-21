"""Arxiv API client for fetching recent papers."""

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
RETRY_BACKOFF = [600, 600, 600, 600]  # 10min fixed interval to outlast rate-limit windows


def _urlopen_with_retry(req, timeout=60):
    """urlopen with exponential backoff on 429/5xx/network errors."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF[attempt]
                print(f"  ArXiv API {e.code}, retry {attempt+1}/{MAX_RETRIES} in {wait}s...")
                time.sleep(wait)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF[attempt]
                print(f"  ArXiv network error ({e}), retry {attempt+1}/{MAX_RETRIES} in {wait}s...")
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
    return all_papers


def fetch_papers(
    categories: list[str],
    max_results: int = 100,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[RawPaper]:
    """Fetch papers from multiple categories, deduplicated by arxiv_id."""
    seen = set()
    papers = []

    for cat in categories:
        if date_from and date_to:
            cat_papers = fetch_category_all(cat, date_from=date_from, date_to=date_to, max_per_page=max_results)
        else:
            cat_papers = fetch_category(cat, max_results=max_results, date=date)
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
