# Pure HTML parsing for Profesia.sk listing pages. No network access here.

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .models import Job, Salary

BASE_URL = "https://www.profesia.sk"

_NUM = r"\d[\d ]*(?:,\d+)?"
_SALARY_RE = re.compile(
    rf"^(?P<from>od\s+)?(?P<a>{_NUM})\s*(?:-\s*(?P<b>{_NUM})\s*)?"
    r"(?P<cur>EUR|€|Kč|CZK)\s*/\s*(?P<per>mesiac|hod)\.?",
    re.IGNORECASE,
)
_CURRENCIES = {"eur": "EUR", "€": "EUR", "kč": "CZK", "czk": "CZK"}
_PERIODS = {"mesiac": "month", "hod": "hour"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _number(text: str) -> float | int:
    value = float(text.replace(" ", "").replace(",", "."))
    return int(value) if value.is_integer() else value


def parse_salary(text: str | None) -> Salary | None:
    # Parse e.g. 'Od 3 900 EUR/mesiac' or '1 500 - 2 000 EUR/mesiac'.
    #
    # Unrecognised formats keep the raw text with the structured fields set to None.
    if not text or not (raw := _clean(text)):
        return None
    m = _SALARY_RE.match(raw)
    if not m:
        return Salary(raw=raw, min=None, max=None, currency=None, period=None)
    low = _number(m["a"])
    if m["b"]:
        high = _number(m["b"])
    else:
        high = None if m["from"] else low
    return Salary(
        raw=raw,
        min=low,
        max=high,
        currency=_CURRENCIES[m["cur"].lower()],
        period=_PERIODS[m["per"].lower()],
    )


def _text(row: Tag, selector: str) -> str | None:
    el = row.select_one(selector)
    return _clean(el.get_text()) if el else None


def parse_row(row: Tag) -> Job | None:
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
    )


def parse_listing(html: str) -> tuple[list[Job], bool]:
    # Return (jobs on this page, whether a next page exists).
    soup = BeautifulSoup(html, "html.parser")
    jobs = [job for row in soup.select("li.list-row") if (job := parse_row(row))]
    has_next = soup.select_one("ul.pagination a.next") is not None
    return jobs, has_next
