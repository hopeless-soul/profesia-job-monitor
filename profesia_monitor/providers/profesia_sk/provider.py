# Profesia.sk as a job source: every configured search, scraped in turn

from __future__ import annotations

import requests

from ...models import Job
from ...sources import SourceProvider, SourceProviderError
from .config import ProfesiaConfig
from .scraper import scrape


# Profesia failure (first page unreachable); run_once retries the source, then logs it and runs the others
class ProfesiaError(SourceProviderError):
    pass


class ProfesiaProvider(SourceProvider):
    name = "profesia"
    label = "Profesia"

    def __init__(self, config: ProfesiaConfig, session: requests.Session | None = None):
        self.config, self.session, self.retry = config, session, config.retry

    # Scrape every search; a first-page failure becomes ProfesiaError
    def fetch(self, known) -> list[Job]:
        jobs: list[Job] = []
        try:
            for query in self.config.searches:
                jobs += scrape(
                    query, source=self.name, max_pages=self.config.pages, delay=self.config.delay, session=self.session,
                )
        except requests.RequestException as e:
            raise ProfesiaError(str(e)) from e
        return jobs
