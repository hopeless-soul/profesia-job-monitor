# Pure HTML parsing for Profesia.sk listing pages. No network access here.

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ...models import Job, Salary
from .errors import ProfesiaLayoutError

BASE_URL = "https://www.profesia.sk"

_NUM = r"\d[\d ]*(?:,\d+)?"
# Optional 'Od' (from) or 'Do' (up to) prefix, one amount or a range, currency, period.
# The trailing $ anchors the end so a second price after the period makes the whole label fail,
# instead of being silently dropped.
# English words (from, to, per, month, hour, year) are accepted too, next to the Slovak ones.
_SALARY_RE = re.compile(
    rf"^(?:(?P<from>od|from)\s+|(?P<to>do|to)\s+)?(?P<a>{_NUM})\s*(?:-\s*(?P<b>{_NUM})\s*)?"
    r"(?P<cur>EUR|€|Kč|CZK|USD)\s*(?:/|\s+per\s+)\s*(?P<per>mesiac|hod|rok|month|hour|year)\.?\s*$",
    re.IGNORECASE,
)
_CURRENCIES = {"eur": "EUR", "€": "EUR", "kč": "CZK", "czk": "CZK", "usd": "USD"}
# Map the Slovak and English period words to the Period literal stored in the model.
_PERIODS = {"mesiac": "month", "hod": "hour", "rok": "year", "month": "month", "hour": "hour", "year": "year"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()

def _number(text: str) -> float | int:
    value = float(text.replace(" ", "").replace(",", "."))
    return int(value) if value.is_integer() else value


def parse_salary(text: str | None) -> Salary | None:
    # Unrecognised formats keep the raw text with the structured fields set to None.
    # e.g. 'Od 3 900 EUR/mesiac' or '1 500 - 2 000 EUR/mesiac'.
    if not text or not (raw := _clean(text)):
        return None
    salary_match = _SALARY_RE.match(raw)
    if not salary_match:
        return Salary(raw=raw, min=None, max=None, currency=None, period=None)
    # Work out min and max from the prefix and whether a range was given.
    first_amount = _number(salary_match["a"])
    if salary_match["b"]:
        # Explicit range: '1 500 - 2 000 EUR'.
        min_value, max_value = first_amount, _number(salary_match["b"])
    elif salary_match["to"]:
        # 'Do 2 000 EUR': upper bound only.
        min_value, max_value = None, first_amount
    elif salary_match["from"]:
        # 'Od 3 900 EUR': lower bound only.
        min_value, max_value = first_amount, None
    else:
        # Single exact figure: min and max are both the value.
        min_value, max_value = first_amount, first_amount
    return Salary(
        raw=raw,
        min=min_value,
        max=max_value,
        currency=_CURRENCIES[salary_match["cur"].lower()],
        period=_PERIODS[salary_match["per"].lower()],
    )


def _text(row: Tag, selector: str) -> str | None:
    el = row.select_one(selector)
    return _clean(el.get_text()) if el else None


def parse_row(row: Tag, source: str) -> Job | None:
    # Parse one `li.list-row` card. Returns None for rows that are not offers.
    link = row.select_one("h2 a[id^=offer]")
    if not link:
        return None

    company_id = None
    logo = row.select_one("a.offer-company-logo-link[href]")
    if logo and (m := re.search(r"/C(\d+)$", logo["href"])):
        company_id = int(m[1])

    salary = None
    labels: list[str] = []
    for label in row.select(".label-group a"):
        if label.get("data-dimension7") == "Salary label":
            salary = parse_salary(label.get_text())
        elif text := _clean(label.get_text()):
            labels.append(text)

    return Job(
        id=int(link["id"].removeprefix("offer")),
        title=_clean(link.get_text()),
        url=BASE_URL + link["href"].split("?")[0],
        company=_text(row, ".employer"),
        company_id=company_id,
        location=_text(row, ".job-location"),
        salary=salary,
        labels=labels,
        posted_text=_text(row, ".list-footer .info"),
        source=source,
    )


def parse_listing(html: str, source: str) -> tuple[list[Job], bool]:
    # Return (jobs on this page, whether a next page exists).
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("li.list-row")
    jobs = [job for row in rows if (job := parse_row(row, source))]
    # rows are there but none parse: the markup changed, which is not the same as an empty search
    if rows and not jobs:
        raise ProfesiaLayoutError(f"{len(rows)} listing rows, none parsed: the page layout may have changed")
    has_next = soup.select_one("ul.pagination a.next") is not None
    return jobs, has_next
