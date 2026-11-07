# Fetch Profesia.sk listing pages; the URL and filters come from Query in config.py.

from __future__ import annotations

import logging
import time

import requests

from ...http import USER_AGENT, retry_after_seconds
from ...models import Job
from ..base import SourceProviderError
from .config import Query
from .errors import ProfesiaBlockedError, ProfesiaError, ProfesiaRateLimitedError, ProfesiaUnavailableError
from .parser import parse_listing

log = logging.getLogger(__name__)


# One listing page, or a typed error for the cause: no answer, rate limited, refused, or another HTTP error
def _fetch_page(session: requests.Session, url: str, params: dict) -> requests.Response:
    try:
        resp = session.get(url, params=params, timeout=20)
    except requests.RequestException as e:  # timeout, DNS, connection reset
        raise ProfesiaUnavailableError(str(e)) from e
    if resp.status_code == 429:
        raise ProfesiaRateLimitedError("HTTP 429", retry_after=retry_after_seconds(resp))
    if resp.status_code in (401, 403):
        raise ProfesiaBlockedError(f"HTTP {resp.status_code}")
    if resp.status_code >= 500:
        raise ProfesiaUnavailableError(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise ProfesiaError(f"HTTP {resp.status_code}")
    return resp


def scrape(
    query: Query,
    *,
    source: str,
    max_pages: int = 3,
    delay: float = 1.5,
    session: requests.Session | None = None,
) -> list[Job]:
    # Scrape up to `max_pages` pages. Duplicate IDs across pages are dropped.
    #
    # A failure on the first page raises; a failure on a later page is logged and
    # the jobs collected so far are returned.
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    url = query.url()
    jobs: dict[int, Job] = {}
    # totals for the one summary log line at the end
    fetched = 0
    pages = 0

    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(delay)
        try:
            resp = _fetch_page(session, url, query.params(page))
            page_jobs, has_next = parse_listing(resp.text, source)
        except SourceProviderError:
            # the first page has nothing to keep, so its failure is raised; later pages keep what they got
            if page == 1:
                raise
            log.warning("page %d failed, keeping %d jobs", page, len(jobs), exc_info=True)
            break

        pages += 1
        fetched += len(page_jobs)
        for job in page_jobs:
            jobs.setdefault(job.id, job)
        if not page_jobs or not has_next:
            break

    log.info("%r: %d jobs from %d pages", query.keyword, fetched, pages)
    return list(jobs.values())
