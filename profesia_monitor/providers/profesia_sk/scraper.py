# Fetch Profesia.sk listing pages. See salvage.md for URL structure and filters.

from __future__ import annotations

import logging
import time

import requests

from ...models import Job
from .config import Query
from .parser import parse_listing

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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
            resp = session.get(url, params=query.params(page), timeout=20)
            resp.raise_for_status()
        except requests.RequestException:
            if page == 1:
                raise
            log.warning("page %d failed, keeping %d jobs", page, len(jobs), exc_info=True)
            break

        page_jobs, has_next = parse_listing(resp.text, source)
        pages += 1
        fetched += len(page_jobs)
        for job in page_jobs:
            jobs.setdefault(job.id, job)
        if not page_jobs or not has_next:
            break

    log.info("%r: %d jobs from %d pages", query.keyword, fetched, pages)
    return list(jobs.values())
