from pathlib import Path

import pytest
import requests

from offer_monitor.providers.profesia_sk.config import ProfesiaConfig, Query
from offer_monitor.providers.profesia_sk.errors import (
    ProfesiaBlockedError,
    ProfesiaError,
    ProfesiaRateLimitedError,
    ProfesiaUnavailableError,
)
from offer_monitor.providers.profesia_sk.provider import ProfesiaProvider
from offer_monitor.providers.profesia_sk.scraper import scrape

PAGE = (Path(__file__).parent / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")
# the same page with a "next" link, so the scraper asks for page 2
PAGE_WITH_NEXT = PAGE + '<ul class="pagination"><li><a class="next" href="#">next</a></li></ul>'


# A page response: body text, status code and headers
class Response:
    def __init__(self, text=PAGE, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


# Session stand-in: returns the responses in order, repeating the last (an exception instance is raised instead)
class Session:
    def __init__(self, *responses):
        self.responses = list(responses) or [Response()]
        self.headers = {}
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


# each failure cause becomes its own error, with the Retry-After seconds when the server sent them
@pytest.mark.parametrize("response, expected, retry_after", [
    (Response(status_code=429, headers={"Retry-After": "7"}), ProfesiaRateLimitedError, 7.0),
    (Response(status_code=403), ProfesiaBlockedError, None),
    (Response(status_code=503), ProfesiaUnavailableError, None),
    (Response(status_code=404), ProfesiaError, None),
    (requests.Timeout("slow"), ProfesiaUnavailableError, None),
])
def test_failure_maps_to_its_error(response, expected, retry_after):
    with pytest.raises(expected) as info:
        scrape(Query(keyword="c#"), source="profesia", max_pages=1, delay=0, session=Session(response))

    assert info.value.retry_after == retry_after


# a failure on a later page keeps the jobs from the pages before it
def test_later_page_failure_keeps_earlier_jobs():
    session = Session(Response(PAGE_WITH_NEXT), Response(status_code=429))

    jobs = scrape(Query(keyword="c#"), source="profesia", max_pages=2, delay=0, session=session)

    assert jobs
    assert session.calls == 2


# the provider runs every configured search and tags each job with its name
def test_provider_fetches_every_search():
    session = Session()
    provider = ProfesiaProvider(ProfesiaConfig(searches=[Query(keyword="c#"), Query(keyword="java")], pages=1, delay=0), session=session)

    jobs = provider.fetch()

    assert session.calls == 2
    assert jobs and {j.source for j in jobs} == {"profesia"}
