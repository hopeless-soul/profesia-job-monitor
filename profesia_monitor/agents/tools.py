# Tools the agents can call.

from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from ..scraper import USER_AGENT

MAX_PAGE_CHARS = 20_000  # keeps one page well inside the model's context


# Download a web page and return its visible text (any host is allowed)
@tool(description="Fetch a web page (job offer or company site) and return its visible text.")
def fetch_offer(url: str) -> str:
    # download; errors go back to the model as text so it can answer UNREACHABLE
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"error: {e}"

    # drop scripts and styles, keep readable text
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:MAX_PAGE_CHARS]
