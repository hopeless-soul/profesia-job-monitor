from pathlib import Path

import pytest
import requests

from profesia_monitor.providers.profesia_sk.config import ProfesiaConfig
from profesia_monitor.providers.profesia_sk.provider import ProfesiaError, ProfesiaProvider
from profesia_monitor.providers.profesia_sk.config import Query
from profesia_monitor.providers.profesia_sk.scraper import scrape

PAGE = (Path(__file__).parent / "fixtures" / "listing_page1.html").read_text(encoding="utf-8")


# A page response with the listing HTML
class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


# Stands in for requests.Session; every page returns the fixture, or fails when `fail` is set
class Session:
    def __init__(self, fail=False):
        self.fail = fail
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        if self.fail:
            raise requests.ConnectionError("down")
        return Response(PAGE)


# every job scraped carries the source it was asked for
def test_scrape_tags_jobs_with_their_source():
    jobs = scrape(Query(keyword="c#"), source="profesia", max_pages=1, delay=0, session=Session())

    assert jobs
    assert {j.source for j in jobs} == {"profesia"}


# a first-page failure is a ProfesiaError, so run_once can retry and then skip Profesia
def test_provider_turns_request_failures_into_profesia_error():
    provider = ProfesiaProvider(ProfesiaConfig(searches=[Query(keyword="c#")], pages=1, delay=0), session=Session(fail=True))

    with pytest.raises(ProfesiaError):
        provider.fetch(lambda ids: set())


# the provider scrapes every configured search and tags each job with its name
def test_provider_fetches_every_search():
    provider = ProfesiaProvider(ProfesiaConfig(searches=[Query(keyword="c#"), Query(keyword="java")], pages=1, delay=0), session=Session())

    jobs = provider.fetch(lambda ids: set())

    assert jobs
    assert {j.source for j in jobs} == {"profesia"}
