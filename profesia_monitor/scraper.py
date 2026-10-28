# Fetch Profesia.sk listing pages. See salvage.md for URL structure and filters.

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Literal

import requests

from .models import Job
from .parser import BASE_URL, parse_listing

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass
class Query:
    keyword: str
    # Path-segment filters, e.g. ["bratislavsky-kraj", "informacne-technologie"].
    segments: list[str] = field(default_factory=list)
    # 0 = on-site only, 1 = remote only, 2 = partially remote.
    remote_work: Literal[0, 1, 2] | None = None
    min_salary: int | None = None
    salary_period: Literal["m", "h"] = "m"
    # Only offers posted in the last N days. Offers expire after ~31 days,
    # so values >= 31 behave like no filter.
    count_days: int | None = None

    def url(self) -> str:
        for s in self.segments:
            if not _SLUG_RE.match(s):
                raise ValueError(f"invalid path segment: {s!r}")
        path = "".join(f"{s}/" for s in self.segments)
        return f"{BASE_URL}/praca/{path}"

    def params(self, page: int) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "search_anywhere": self.keyword,
            "sort_by": "relevance",
            "page_num": page,
        }
        if self.remote_work is not None:
            params["remote_work"] = self.remote_work
        if self.min_salary is not None:
            params["salary"] = self.min_salary
            params["salary_period"] = self.salary_period
        if self.count_days is not None:
            params["count_days"] = self.count_days
        return params


def scrape(
    query: Query,
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

        page_jobs, has_next = parse_listing(resp.text)
        log.info("page %d: %d jobs", page, len(page_jobs))
        for job in page_jobs:
            jobs.setdefault(job.id, job)
        if not page_jobs or not has_next:
            break

    return list(jobs.values())
