import pytest
import requests

from profesia_monitor.agents import tools
from profesia_monitor.agents.tools import fetch_offer


class FakeResponse(requests.Response):
    def __init__(self, status, headers=None, text="<p>offer</p>"):
        super().__init__()
        self.status_code = status
        self.headers.update(headers or {})
        self._content = text.encode()


# requests.get stand-in that answers from a list; sleeps are recorded instead of waited
@pytest.fixture
def server(monkeypatch):
    state = {"responses": [], "calls": 0, "sleeps": []}

    def get(url, **kwargs):
        state["calls"] += 1
        return state["responses"].pop(0)

    monkeypatch.setattr(tools.requests, "get", get)
    monkeypatch.setattr(tools.time, "sleep", state["sleeps"].append)
    monkeypatch.setattr(tools.random, "uniform", lambda a, b: 0)
    return state


# a 429 is retried after the backoff step, then the page is returned
def test_rate_limit_is_retried(server):
    server["responses"] = [FakeResponse(429), FakeResponse(503), FakeResponse(200)]

    assert fetch_offer.invoke({"url": "https://x"}) == "offer"
    assert server["sleeps"] == [5, 15]


# Retry-After wins over the backoff step, capped at MAX_RETRY_AFTER
def test_retry_after_header_is_honoured_and_capped(server):
    server["responses"] = [FakeResponse(429, {"Retry-After": "30"}), FakeResponse(429, {"Retry-After": "9999"}), FakeResponse(200)]

    fetch_offer.invoke({"url": "https://x"})

    assert server["sleeps"] == [30, tools.MAX_RETRY_AFTER]


# when retries run out the error goes back to the model; other errors aren't retried
def test_gives_up_after_last_retry_and_skips_other_errors(server):
    server["responses"] = [FakeResponse(429)] * 4
    assert fetch_offer.invoke({"url": "https://x"}).startswith("error: 429")
    assert server["calls"] == 4

    server["responses"] = [FakeResponse(404)]
    assert fetch_offer.invoke({"url": "https://x"}).startswith("error: 404")
    assert server["calls"] == 5
