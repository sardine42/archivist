"""Arxiv API client for fetching recent papers."""

import email.utils
import fcntl
import http.client
import os
import random
import re
import threading
import time
import unicodedata
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
ARXIV_USER_AGENT = "MyArchivist/0.1"

# OAI-PMH and the Atom API use independent hosts and can fail independently.
# Prefer OAI-PMH and retain the Atom API as fallback. Address the OAI host
# directly to avoid an unnecessary redirect and route-dependent errors.
OAI_API = "https://oaipmh.arxiv.org/oai"
OAI_NS = "{http://arxiv.org/OAI/arXiv/}"
OAI_RAW_NS = "{http://arxiv.org/OAI/arXivRaw/}"
OAI_PMH_NS = "{http://www.openarchives.org/OAI/2.0/}"
# OAI datestamps are *announcement* dates, which trail submission by a day and by
# a whole weekend for Friday submissions. Four days is the smallest window that
# never truncates one.
OAI_ANNOUNCE_WINDOW_DAYS = 4
# How far back a late-announced paper may have been submitted and still be
# picked up by the run whose window it finally appears in (see
# fetch_papers_oai's ``late_since``).
OAI_LATE_LOOKBACK_DAYS = 14
# Transport, not arXiv, is what fails on these pages — IncompleteRead and TLS
# resets on both routes — and a retry almost always clears it.
OAI_MAX_RETRIES = 8

# arXiv's legacy API limit applies to all clients on a machine as a whole:
# one connection at a time and no more than one request every three seconds.
# A process-local ``sleep(3)`` cannot enforce that when digest/read-paper jobs
# overlap, so serialize API requests through a shared lock. The lock file stores
# the next allowed request time, including any server-directed backoff window.
ARXIV_REQUEST_INTERVAL = 3.1
ARXIV_REQUEST_LOCK = "/tmp/archivist-arxiv-api.lock"
_ARXIV_REQUEST_MUTEX = threading.Lock()

MAX_RETRIES = 4
# Escalating 429 backoff (seconds), indexed by attempt + jitter. A short first
# wait recovers from brief rate-limit windows in ~1min instead of stalling 10min;
# later waits grow to outlast sustained blocks. Worst case ≈ 18min over 4 tries
# (was a flat 600s × 4 = 40min, which disguised rate-limiting as a frozen hang).
RATELIMIT_BACKOFF = (60, 150, 300, 600)
NETWORK_BACKOFF = [5, 15, 30, 60]  # transient network / truncated-read errors — retry quickly
# Wall-clock cap for one-off lookups (fetch_by_id / search_by_title). Batch
# digest fetches deliberately have no cap — outwaiting a rate limit is the job.
SINGLE_LOOKUP_DEADLINE = 120.0
# How many range-resume rounds a truncated PDF download gets before giving up.
RESUME_MAX_ATTEMPTS = 8

# Route availability depends on the host network. Try the environment's proxy
# configuration first and fall back to a direct connection on transport errors.
# Set ARCHIVIST_ARXIV_ROUTE=proxy|direct to pin one route when diagnosing.
_ENV_OPENER = urllib.request.build_opener()  # honours http(s)_proxy + system proxy
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _openers() -> tuple:
    """Openers to try in order for one request."""
    route = (os.environ.get("ARCHIVIST_ARXIV_ROUTE") or "").strip().lower()
    if route == "direct":
        return (_DIRECT_OPENER,)
    if route == "proxy":
        return (_ENV_OPENER,)
    return (_ENV_OPENER, _DIRECT_OPENER)


def _retry_after_seconds(error: urllib.error.HTTPError) -> float | None:
    """Return the server-requested retry delay, if one was supplied."""
    if not error.headers:
        return None
    value = error.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _read_next_allowed(lock_file) -> float:
    lock_file.seek(0)
    try:
        return float(lock_file.read().strip() or 0)
    except ValueError:
        return 0


def _write_next_allowed(lock_file, timestamp: float) -> None:
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(timestamp))
    lock_file.flush()


def _defer_all_requests(delay: float) -> None:
    """Publish a server backoff window to every local Archivist process."""
    with _ARXIV_REQUEST_MUTEX:
        with open(ARXIV_REQUEST_LOCK, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            next_allowed = max(
                _read_next_allowed(lock_file),
                time.time() + delay,
            )
            _write_next_allowed(lock_file, next_allowed)


def _read_rate_limited(req, timeout: int) -> bytes:
    """Perform one request under the machine-wide arXiv API rate limit."""
    with _ARXIV_REQUEST_MUTEX:
        with open(ARXIV_REQUEST_LOCK, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            wait = max(0.0, _read_next_allowed(lock_file) - time.time())
            if wait:
                time.sleep(wait)

            # Persist before opening the connection so a crash still leaves the
            # next process with a conservative request-start timestamp. The flock
            # remains held through the response read, enforcing one connection.
            _write_next_allowed(
                lock_file,
                time.time() + ARXIV_REQUEST_INTERVAL,
            )
            candidates = _openers()
            for index, opener in enumerate(candidates):
                try:
                    return opener.open(req, timeout=timeout).read()
                except urllib.error.HTTPError as e:
                    # Honor rate-limit and server errors without changing routes.
                    # Other HTTP statuses may originate from an intermediary, so
                    # try the fallback route before exhausting the retry budget.
                    if e.code == 429 or e.code >= 500 or index == len(candidates) - 1:
                        raise
                    print(
                        f"  ArXiv route returned HTTP {e.code} "
                        f"(route artifact, not an origin verdict), "
                        f"retrying via fallback route...",
                        flush=True,
                    )
                except (urllib.error.URLError, TimeoutError, OSError,
                        http.client.HTTPException):
                    if index == len(candidates) - 1:
                        raise
                    # Transport-level failure: the configured route is unusable
                    # right now, so try the other one before burning a retry.
                    print("  ArXiv route failed, retrying via fallback route...", flush=True)


def _urlopen_with_retry(req, timeout=60, deadline=None, raise_incomplete=False,
                        max_retries=None):
    """urlopen + read with retry over whichever route to arXiv is usable.

    - 429 / 5xx (rate-limit / server): honor Retry-After when present, otherwise
      use a long jittered backoff shared by every local Archivist process.
    - network / truncated-read errors (URLError, timeout, OSError, and
      ``http.client.HTTPException`` such as ``IncompleteRead``): short escalating
      backoff — these are usually transient one-offs, not rate limits.

    ``deadline`` caps total wall-clock across every attempt. ``timeout`` only
    bounds a single socket, so the retry ladder alone can run ~7min (5 sockets ×
    60s + 110s of sleeps) — long enough that callers with their own timeout kill
    the process mid-flight and leave half-built paper dirs behind. Batch fetches
    want that patience (waiting out a rate limit is the point); a single-paper
    lookup wants to fail fast and let the caller fall back.

    ``max_retries`` overrides the module default for callers that need a larger
    budget to recover from transient transport errors on large OAI pages.
    """
    if max_retries is None:
        max_retries = MAX_RETRIES
    started = time.monotonic()

    def _left():
        return None if deadline is None else deadline - (time.monotonic() - started)

    for attempt in range(max_retries + 1):
        remaining = _left()
        if remaining is not None and remaining <= 0:
            raise TimeoutError(f"arXiv request exceeded {deadline:.0f}s deadline")
        try:
            return _read_rate_limited(
                req,
                timeout if remaining is None else max(1, min(timeout, remaining)),
            )
        except urllib.error.HTTPError as e:
            # 406 belongs here with the 5xx family, not with the client errors:
            # oaipmh.arxiv.org emits it under load, on every route at once, and
            # it clears on its own — but it can stay put for two or three minutes,
            # long enough to look deterministic and abort a whole fetch.
            if (e.code in (406, 429) or e.code >= 500) and attempt < max_retries:
                base = RATELIMIT_BACKOFF[min(attempt, len(RATELIMIT_BACKOFF) - 1)]
                fallback = base + random.uniform(0, base * 0.2)
                wait = max(fallback, _retry_after_seconds(e) or 0)
                # Publish the server's window even when we personally give up —
                # other processes still owe arXiv the wait.
                _defer_all_requests(wait)
                remaining = _left()
                if remaining is not None and wait >= remaining:
                    raise TimeoutError(
                        f"arXiv {e.code}: next retry needs {wait:.0f}s, "
                        f"past the {deadline:.0f}s deadline"
                    ) from e
                print(f"  ArXiv API {e.code}, retry {attempt+1}/{max_retries} in {wait:.0f}s...", flush=True)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            if raise_incomplete and isinstance(e, http.client.IncompleteRead):
                # Surface it now: the caller resumes from the partial body
                # instead of discarding a near-complete download and refetching.
                raise
            if attempt < max_retries:
                wait = NETWORK_BACKOFF[min(attempt, len(NETWORK_BACKOFF) - 1)]
                remaining = _left()
                if remaining is not None and wait >= remaining:
                    raise TimeoutError(
                        f"arXiv network error ({type(e).__name__}), "
                        f"past the {deadline:.0f}s deadline"
                    ) from e
                print(f"  ArXiv network error ({type(e).__name__}: {e}), retry {attempt+1}/{max_retries} in {wait}s...", flush=True)
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
    return fetch_categories(
        [category],
        max_results=max_results,
        date=date,
        date_from=date_from,
        date_to=date_to,
        start_index=start_index,
    )


def fetch_categories(
    categories: list[str],
    max_results: int = 100,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    start_index: int = 0,
) -> list[RawPaper]:
    """Fetch one page for the union of one or more arXiv categories."""
    if not categories:
        return []

    category_query = " OR ".join(f"cat:{category}" for category in categories)
    if len(categories) > 1:
        category_query = f"({category_query})"

    if date_from and date_to:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to = datetime.strptime(date_to, "%Y-%m-%d")
        start = dt_from.strftime("%Y%m%d0000")
        end = (dt_to + timedelta(days=1)).strftime("%Y%m%d0000")
        query = f"{category_query} AND submittedDate:[{start} TO {end}]"
    elif date:
        dt = datetime.strptime(date, "%Y-%m-%d")
        start = dt.strftime("%Y%m%d0000")
        end = (dt + timedelta(days=1)).strftime("%Y%m%d0000")
        query = f"{category_query} AND submittedDate:[{start} TO {end}]"
    else:
        query = category_query

    params = urllib.parse.urlencode({
        "search_query": query,
        "start": start_index,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })

    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": ARXIV_USER_AGENT})

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
    return fetch_categories_all(
        [category],
        date_from=date_from,
        date_to=date_to,
        max_per_page=max_per_page,
        max_total=max_total,
    )


def fetch_categories_all(
    categories: list[str],
    date_from: str,
    date_to: str,
    max_per_page: int = 200,
    max_total: int = 2000,
) -> list[RawPaper]:
    """Fetch all papers in a date range for a union of categories."""
    all_papers = []
    start = 0
    while start < max_total:
        batch = fetch_categories(
            categories,
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
    else:
        # Ran the range dry only if we broke out above; falling through the
        # while condition means max_total cut us off. Never let that be silent.
        print(
            f"  ⚠ {','.join(categories)} {date_from}..{date_to}: "
            f"hit max_total={max_total}, "
            f"results TRUNCATED — raise max_total or narrow the range",
            flush=True,
        )
    return all_papers


# arXivRaw carries titles, abstracts and author names as the submitter's raw
# TeX, where the Atom feed serves them already decoded ("S{\\l}awomir" vs
# "Sławomir"). Only the accent and ligature commands are worth decoding — they
# are what shows up in names — and only the braces those commands consume are
# removed, so math like ``x^{2}`` in a title survives untouched.
_TEX_COMBINING = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "k": "\u0328",
    "c": "\u0327", "r": "\u030a", "H": "\u030b",
}
_TEX_LIGATURES = {
    "l": "\u0142", "L": "\u0141", "o": "\u00f8", "O": "\u00d8", "ss": "\u00df",
    "ae": "\u00e6", "AE": "\u00c6", "oe": "\u0153", "OE": "\u0152",
    "aa": "\u00e5", "AA": "\u00c5", "i": "i", "j": "j",
}
# Either fully braced ("{\\l}") or bare ("\\l" not starting a longer command).
# An optional-brace pattern cannot express this: in "S{\\l}awomir" it would
# backtrack to leave the closing brace behind.
_TEX_LIGATURE_RE = re.compile(
    r"\{\\(ss|ae|AE|oe|OE|aa|AA|[lLoOij])\}"
    r"|\\(ss|ae|AE|oe|OE|aa|AA|[lLoOij])(?![a-zA-Z])"
)
_TEX_ACCENT_RE = re.compile(
    r"\\(['`^\"~=.]|(?<=\\)[uvkcrH](?=[\s{]))\s*\{?([A-Za-z])\}?"
)


def _detex(text: str) -> str:
    """Best-effort decode of TeX accents/ligatures in arXivRaw text fields."""
    if not text or "\\" not in text:
        return text
    text = _TEX_LIGATURE_RE.sub(
        lambda m: _TEX_LIGATURES[m.group(1) or m.group(2)], text)
    text = re.sub(
        r"\\(['`^\"~=.uvkcrH])\s*\{?([A-Za-z])\}?",
        lambda m: (m.group(2) + _TEX_COMBINING[m.group(1)]
                   if m.group(1) in _TEX_COMBINING else m.group(0)),
        text,
    )
    return unicodedata.normalize("NFC", text)


def _oai_author_names(raw: str) -> list[str]:
    """Split arXivRaw's single authors string into names.

    The field is one free-text string rather than the per-author elements the
    Atom feed carries, and arXiv accepts both conventions in it:

        "Ruilin Wang and Xiaodai Dong"              -> BibTeX: " and " separates
        "Benyu Zhang, Qiang Zhang, Rui Li"          -> plain list: "," separates

    A comma therefore means opposite things in the two forms, so the presence of
    " and " decides which one is in play before any comma is touched: inside a
    BibTeX string a comma is the "Last, First" inversion, never a separator.
    """
    raw = " ".join((raw or "").split())
    if not raw:
        return []

    def _flip(name: str) -> str:
        """"Liepins, Vilnis" -> "Vilnis Liepins"; anything else unchanged."""
        if name.count(",") != 1:
            return name
        last, _, first = name.partition(",")
        first, last = first.strip(), last.strip()
        return f"{first} {last}" if first and last else name

    if re.search(r"\s+and\s+", raw):
        chunks = re.split(r"\s+and\s+", raw)
    elif raw.count(",") == 1:
        # Ambiguous: one comma, no " and ". Two-author plain lists are written
        # with " and " in practice, so read it as a single inverted name.
        chunks = [raw]
    else:
        chunks = raw.split(",")

    names = []
    for chunk in chunks:
        name = _flip(" ".join(chunk.split()).strip(" ,"))
        if name:
            names.append(name)
    return names


def _parse_oai_page(xml_data: bytes) -> tuple[list[dict], str]:
    """Parse one OAI-PMH arXivRaw ListRecords page into dicts + resumption token."""
    root = ET.fromstring(xml_data)
    records = []

    for rec in root.iter(f"{OAI_PMH_NS}record"):
        header = rec.find(f"{OAI_PMH_NS}header")
        if header is not None and header.get("status") == "deleted":
            continue
        metadata = rec.find(f"{OAI_PMH_NS}metadata")
        if metadata is None:
            continue
        meta = metadata.find(f"{OAI_RAW_NS}arXivRaw")
        if meta is None:
            continue

        # <version version="vN"><date>RFC-2822</date></version>, in order.
        versions = []
        for version in meta.findall(f"{OAI_RAW_NS}version"):
            label = (version.get("version") or "").lstrip("v")
            date_text = (version.findtext(f"{OAI_RAW_NS}date") or "").strip()
            if not label.isdigit() or not date_text:
                continue
            try:
                stamp = email.utils.parsedate_to_datetime(date_text)
            except (TypeError, ValueError):
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            versions.append((
                int(label),
                stamp.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            ))
        if not versions:
            continue
        versions.sort()

        records.append({
            "id": (meta.findtext(f"{OAI_RAW_NS}id") or "").strip(),
            "v1_date": versions[0][1],
            "latest_version": versions[-1][0],
            "latest_date": versions[-1][1],
            "title": _detex(" ".join((meta.findtext(f"{OAI_RAW_NS}title") or "").split())),
            "abstract": _detex(" ".join((meta.findtext(f"{OAI_RAW_NS}abstract") or "").split())),
            "categories": (meta.findtext(f"{OAI_RAW_NS}categories") or "").split(),
            "authors": [_detex(n) for n in
                        _oai_author_names(meta.findtext(f"{OAI_RAW_NS}authors") or "")],
        })

    token = ""
    token_elem = root.find(f".//{OAI_PMH_NS}resumptionToken")
    if token_elem is not None and token_elem.text:
        token = token_elem.text.strip()
    return records, token


def fetch_papers_oai(
    categories: list[str],
    date: str,
    late_since: str | None = None,
) -> list[RawPaper]:
    """Fetch papers first submitted on ``date`` via OAI-PMH.

    With ``late_since``, also return papers first submitted on
    ``[late_since, date)`` that turn up in this window. Those are late
    announcements: arXiv normally announces a submission the next cycle, but a
    held paper can surface a day or more later, by which time the run for its
    own submission date has already looked and the run for the next date
    rejects it on v1 date. The caller is responsible for dropping records it has
    already seen, since revised papers also resurface in this feed.

    The OAI feed is filtered by *announcement* datestamp, not submission date,
    so the window starts the day after ``date`` and runs far enough to clear a
    weekend. What makes the result equivalent to the Atom API's
    ``submittedDate`` query is the arXivRaw metadata format: it carries the
    full version history, so "first submitted on ``date``" is a direct test of
    v1's own date rather than an inference.

    The simpler ``arXiv`` format cannot do this. Its ``<created>`` is the date
    the *current* version was submitted — a 2025 paper revised on ``date``
    reports ``created=date`` — and it carries no version number at all, which
    silently turns every revision of an archived paper into a "same version,
    already have it" and suppresses the update path. Revision records therefore
    require the full version history rather than the current-version date.

    Trade-off: arXivRaw has no per-author affiliation element. The pipeline can
    populate affiliations after reading the PDF.

    Known limit: OAI only lists papers that have been *announced*. arXiv
    announces once a day and anything submitted after that day's cutoff waits
    for the next cycle, so a digest run on ``date + 1`` cannot see the tail of
    ``date``'s submissions — the window is clamped at today, and the records
    land under a datestamp that does not exist yet. A later backfill can recover
    them. The daily pipeline passes ``late_since`` to catch this tail on a
    later run without requiring a separate backfill.
    """
    day = datetime.strptime(date, "%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    window = []
    for offset in range(1, OAI_ANNOUNCE_WINDOW_DAYS + 1):
        stamp = (day + timedelta(days=offset)).strftime("%Y-%m-%d")
        if stamp > today:
            break
        window.append(stamp)
    if not window:
        window = [today]

    wanted = set(categories)
    seen: set[str] = set()
    papers: list[RawPaper] = []

    # One category-level set per configured category, one day per request.
    # Smaller category-level responses reduce transport truncation. A paper
    # carries a setSpec for every category it is filed under, cross-lists
    # included, so this is the same union the Atom API's
    # "cat:A OR cat:B" query returns; ``seen`` drops the cross-list duplicates.
    sets = []
    for category in categories:
        archive, _, subject = category.partition(".")
        sets.append(f"{archive}:{archive}:{subject}" if subject else archive)

    for stamp in window:
        for oai_set in sets:
            token = ""
            while True:
                if token:
                    params = {"verb": "ListRecords", "resumptionToken": token}
                else:
                    params = {
                        "verb": "ListRecords",
                        "set": oai_set,
                        "metadataPrefix": "arXivRaw",
                        "from": stamp,
                        "until": stamp,
                    }
                url = f"{OAI_API}?{urllib.parse.urlencode(params)}"
                req = urllib.request.Request(url, headers={
                    "User-Agent": ARXIV_USER_AGENT,
                    # Without an explicit Accept this host intermittently
                    # answers 406 instead of serving the page.
                    "Accept": "*/*",
                })
                # Allow a larger socket timeout for potentially slow OAI
                # responses while retaining the bounded retry budget.
                xml_data = _urlopen_with_retry(req, timeout=180,
                                               max_retries=OAI_MAX_RETRIES)
                records, token = _parse_oai_page(xml_data)

                for record in records:
                    arxiv_id = record["id"]
                    if not arxiv_id or arxiv_id in seen:
                        continue
                    v1_date = record["v1_date"]
                    on_time = v1_date == date
                    late = bool(late_since) and late_since <= v1_date < date
                    if not (on_time or late):
                        continue  # first submitted on some other day
                    if wanted and not (set(record["categories"]) & wanted):
                        continue
                    seen.add(arxiv_id)
                    version = record["latest_version"]
                    papers.append(RawPaper(
                        arxiv_id=f"{arxiv_id}v{version}",
                        title=record["title"],
                        authors=record["authors"],
                        affiliations=[],
                        abstract=record["abstract"],
                        categories=record["categories"],
                        published=record["v1_date"],
                        updated=record["latest_date"],
                        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                    ))

                if not token:
                    break

    return papers


def fetch_papers(
    categories: list[str],
    max_results: int = 100,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    late_since: str | None = None,
) -> list[RawPaper]:
    """Fetch papers from multiple categories, deduplicated by arxiv_id.

    ``late_since`` applies to the OAI ``date`` path only (see fetch_papers_oai);
    the Atom API fallback queries by submission date and cannot express it.

    Every date-filtered path paginates until the day/range is exhausted, so
    ``max_results`` is a *page size*, not a ceiling on what a day can yield.
    A single-page ceiling with ``sortOrder=descending`` would drop the day's
    earliest submissions before keyword filtering whenever the category count
    exceeded the page size. Pagination preserves the complete candidate set.
    """
    seen = set()
    papers = []

    if date_from and date_to:
        # One Boolean query is substantially cheaper than one request stream per
        # category, while returning the same deduplicated union.
        papers = fetch_categories_all(
            categories,
            date_from=date_from,
            date_to=date_to,
            max_per_page=max_results,
            max_total=2000 * max(1, len(categories)),
        )
        return papers
    if date:
        # Prefer OAI-PMH: it lives in a different quota bucket from /api/query
        # (see OAI_API), needs one request per day instead of a dozen paginated
        # ones, and returns the same candidate set. ARCHIVIST_ARXIV_SOURCE pins
        # one source when diagnosing.
        source = (os.environ.get("ARCHIVIST_ARXIV_SOURCE") or "").strip().lower()
        if source != "api":
            try:
                return fetch_papers_oai(categories, date, late_since=late_since)
            except Exception as exc:
                if source == "oai":
                    raise
                print(
                    f"  OAI-PMH unavailable ({type(exc).__name__}: {exc}), "
                    f"falling back to the Atom API...",
                    flush=True,
                )
        papers = fetch_categories_all(
            categories,
            date_from=date,
            date_to=date,
            max_per_page=max_results,
            max_total=2000 * max(1, len(categories)),
        )
        return papers

    for cat in categories:
        # No date filter: "the most recent N per category" — preserve the
        # existing per-category cap and merge semantics.
        cat_papers = fetch_category(cat, max_results=max_results)
        for p in cat_papers:
            if p.arxiv_id not in seen:
                seen.add(p.arxiv_id)
                papers.append(p)

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


def fetch_by_id_oai(arxiv_id: str) -> RawPaper | None:
    """Fetch one paper's metadata via OAI-PMH GetRecord."""
    clean = arxiv_id.split("v")[0] if re.match(r"^\d{4}\.\d{4,5}v\d+$", arxiv_id) else arxiv_id
    params = {
        "verb": "GetRecord",
        "identifier": f"oai:arXiv.org:{clean}",
        "metadataPrefix": "arXivRaw",
    }
    url = f"{OAI_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": ARXIV_USER_AGENT,
        "Accept": "*/*",
    })
    xml_data = _urlopen_with_retry(
        req, timeout=60, deadline=SINGLE_LOOKUP_DEADLINE, max_retries=2
    )
    records, _ = _parse_oai_page(xml_data)
    if not records:
        return None
    record = records[0]
    return RawPaper(
        arxiv_id=f"{record['id']}v{record['latest_version']}",
        title=record["title"],
        authors=record["authors"],
        affiliations=[],
        abstract=record["abstract"],
        categories=record["categories"],
        published=record["v1_date"],
        updated=record["latest_date"],
        pdf_url=f"https://arxiv.org/pdf/{record['id']}",
    )


def fetch_by_id(arxiv_id: str) -> RawPaper | None:
    """Fetch a single paper by arxiv_id.

    Bounded by ``SINGLE_LOOKUP_DEADLINE``: this feeds interactive/scripted paths
    (``download-paper.py``, read-paper) where the metadata is a nice-to-have and
    the caller can carry on without it. Stalling here for minutes only gets the
    whole script killed from outside, which is how half-built paper dirs appear.

    Tries OAI-PMH before the Atom API because the two services can fail
    independently. Missing metadata may leave an empty ``abstract``, so try
    both sources within the lookup deadline.
    """
    source = (os.environ.get("ARCHIVIST_ARXIV_SOURCE") or "").strip().lower()
    if source != "api":
        try:
            paper = fetch_by_id_oai(arxiv_id)
            if paper is not None:
                return paper
        except Exception as exc:
            if source == "oai":
                raise
            print(
                f"  OAI-PMH lookup failed ({type(exc).__name__}: {exc}), "
                f"falling back to the Atom API...",
                flush=True,
            )

    params = urllib.parse.urlencode({"id_list": arxiv_id, "max_results": 1})
    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": ARXIV_USER_AGENT})
    xml_data = _urlopen_with_retry(req, timeout=60, deadline=SINGLE_LOOKUP_DEADLINE)
    results = _parse_feed(xml_data)
    return results[0] if results else None


def search_by_title(title: str, max_results: int = 5) -> list[RawPaper]:
    """Search Arxiv papers by title and return matching results.

    Same deadline rationale as ``fetch_by_id`` — an interactive lookup.
    """
    query = f'ti:"{title}"'
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": ARXIV_USER_AGENT})
    xml_data = _urlopen_with_retry(req, timeout=60, deadline=SINGLE_LOOKUP_DEADLINE)
    return _parse_feed(xml_data)


def download_pdf(url: str, dest: str) -> None:
    """Download a PDF, resuming when the response is cut short.

    Keep partial response bytes after ``IncompleteRead`` and request the
    remainder with a Range header. Resuming avoids repeatedly downloading the
    same prefix. Verify final sizes to handle servers that ignore Range.
    """
    def _attempt(start: int) -> tuple[bytes, bool]:
        """Return (body, complete). ``start`` is the byte offset to resume from."""
        headers = {"User-Agent": ARXIV_USER_AGENT}
        if start:
            headers["Range"] = f"bytes={start}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            return _urlopen_with_retry(req, timeout=120, raise_incomplete=True), True
        except http.client.IncompleteRead as e:
            return e.partial, False

    body, complete = _attempt(0)
    for _ in range(RESUME_MAX_ATTEMPTS):
        if complete:
            break
        tail, complete = _attempt(len(body))
        if not tail:
            break  # server gave us nothing new; stop rather than spin
        body += tail

    if not complete:
        raise OSError(
            f"PDF download from {url} stayed incomplete after "
            f"{RESUME_MAX_ATTEMPTS} resume attempts ({len(body)} bytes)"
        )

    with open(dest, "wb") as f:
        f.write(body)
