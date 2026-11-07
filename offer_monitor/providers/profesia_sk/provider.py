# Profesia.sk as a job source: every configured search, scraped in turn

from __future__ import annotations

import logging

import requests

from ...models import Job
from ..base import SourceProvider
from .config import ProfesiaConfig
from .scraper import scrape

log = logging.getLogger(__name__)


class ProfesiaProvider(SourceProvider):
    name = "profesia"
    label = "Profesia"

    def __init__(self, config: ProfesiaConfig, session: requests.Session | None = None):
        self.config, self.session, self.retry = config, session, config.retry

    # Scrape every search; typed errors from scrape() propagate to fetch_source
    def fetch(self, known) -> list[Job]:
        jobs: list[Job] = []
        for query in self.config.searches:
            jobs += scrape(query, source=self.name, max_pages=self.config.pages, delay=self.config.delay, session=self.session)
        # every search empty can be a quiet day, but it is also what a wrong search looks like, so say it
        if self.config.searches and not jobs:
            log.warning("profesia: no offers in any search; check the searches if this keeps happening")
        return jobs
