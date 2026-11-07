from pathlib import Path

import pytest
import requests

from profesia_monitor.providers.profesia_sk.config import ProfesiaConfig, Query
from profesia_monitor.providers.profesia_sk.errors import (
    ProfesiaBlockedError,
    ProfesiaError,
    ProfesiaRateLimitedError,
    ProfesiaUnavailableError,
)
from profesia_monitor.providers.profesia_sk.provider import ProfesiaProvider
from profesia_monitor.providers.profesia_sk.scraper import scrape

PAGE = (Path(__file__).parent / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")
# the same page with a "next" link, so the scraper asks for page 2
PAGE_WITH_NEXT = PAGE + '<ul class="pagination"><li><a class="next" href="#">next</a></li></ul>'


# A page response: body text, status code and headers
class Response:
    def __init__(self, text=PAGE, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


# Session stand-in: returns the responses in order (an exception instance is raised instead)
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


# every job scraped carries the source it was asked for
def test_scrape_tags_jobs_with_their_source():
    jobs = scrape(Query(keyword="c#"), source="profesia", max_pages=1, delay=0, session=Session())

    assert jobs
    assert {j.source for j in jobs} == {"profesia"}


# each failure cause becomes its own error, with the Retry-After seconds when the server sent them
@pytest.mark.parametrize("response, expected, retry_after", [
    (Response(status_code=429, headers={"Retry-After": "7"}), ProfesiaRateLimitedError, 7.0),
    (Response(status_code=403), ProfesiaBlockedError, None),
    (Response(status_code=503), ProfesiaUnavailableError, None),
    (Response(status_code=404), ProfesiaError, None),
])
def test_http_status_maps_to_its_error(response, expected, retry_after):
    with pytest.raises(expected) as info:
        scrape(Query(keyword="c#"), source="profesia", max_pages=1, delay=0, session=Session(response))

    assert info.value.retry_after == retry_after


# no answer at all (timeout, connection) is an unavailable source
def test_timeout_is_unavailable():
    with pytest.raises(ProfesiaUnavailableError):
        scrape(Query(keyword="c#"), source="profesia", max_pages=1, delay=0, session=Session(requests.Timeout("slow")))


# a failure on a later page keeps the jobs from the pages before it
def test_later_page_failure_keeps_earlier_jobs():
    session = Session(Response(PAGE_WITH_NEXT), Response(status_code=429))

    jobs = scrape(Query(keyword="c#"), source="profesia", max_pages=2, delay=0, session=session)

    assert jobs
    assert session.calls == 2


# the provider runs every configured search and tags each job with its name
def test_provider_fetches_every_search():
    provider = ProfesiaProvider(ProfesiaConfig(searches=[Query(keyword="c#"), Query(keyword="java")], pages=1, delay=0), session=Session())

    jobs = provider.fetch(lambda ids: set())

    assert jobs
    assert {j.source for j in jobs} == {"profesia"}


# a first-page failure reaches the caller as the typed error, not wrapped in a generic one
def test_provider_raises_the_typed_error():
    provider = ProfesiaProvider(ProfesiaConfig(searches=[Query(keyword="c#")], pages=1, delay=0), session=Session(requests.ConnectionError("down")))

    with pytest.raises(ProfesiaUnavailableError):
        provider.fetch(lambda ids: set())
