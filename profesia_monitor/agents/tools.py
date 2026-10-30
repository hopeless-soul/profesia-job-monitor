# Tools the agents can call.

from __future__ import annotations

import logging
import random
import time

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from ..scraper import USER_AGENT

MAX_PAGE_CHARS = 20_000  # keeps one page well inside the model's context

# Rate limits (LinkedIn answers 429 when reviews come fast) and temporary server errors are retried
RETRY_STATUSES = {429, 502, 503, 504}
RETRY_DELAYS = (5, 15, 45)  # seconds before each retry, used when the server sends no Retry-After
MAX_RETRY_AFTER = 120  # cap on a server's Retry-After, so one review can't stall a worker for long

log = logging.getLogger(__name__)


# Seconds from a Retry-After header in its seconds form; None when missing or an HTTP date
def _retry_after(resp: requests.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


# GET with retries on RETRY_STATUSES: waits Retry-After or the next backoff step, plus jitter so
# the two verdict workers don't retry in step; returns the last response when retries run out
def _get(url: str) -> requests.Response:
    for delay in (*RETRY_DELAYS, None):
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code not in RETRY_STATUSES or delay is None:
            return resp
        wait = min(_retry_after(resp) or delay, MAX_RETRY_AFTER) + random.uniform(0, delay / 2)
        log.warning("fetch %s: HTTP %d, retrying in %.0f s", url, resp.status_code, wait)
        time.sleep(wait)
    raise AssertionError("unreachable")


# Download a web page and return its visible text (any host is allowed)
@tool(description="Fetch a web page (job offer or company site) and return its visible text.")
def fetch_offer(url: str) -> str:
    # download; errors go back to the model as text so it can answer UNREACHABLE
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"error: {e}"

    # drop scripts and styles, keep readable text
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:MAX_PAGE_CHARS]
