import asyncio
from pathlib import Path

import pytest

from profesia_monitor import linkedin
from profesia_monitor.config import LinkedInConfig, LinkedInSearch
from profesia_monitor.linkedin import LinkedInError, collect, fetch_jobs, parse_job_details

DETAILS = (Path(__file__).parent / "fixtures" / "linkedin_job_details.txt").read_text(encoding="utf-8")


# header lines become title, company, location, posted text and work type labels
def test_parse_job_details_reads_header():
    job = parse_job_details(4484440903, DETAILS)

    assert (job.title, job.company, job.location) == ("Back End JavaScript Developer", "Ciklum", "Bratislava, Slovakia")
    assert job.posted_text == "1 day ago"
    assert job.labels == ["Hybrid", "Full-time"]  # "Remote first culture" after Apply is ignored
    assert job.url == "https://www.linkedin.com/jobs/view/4484440903/"
    assert (job.source, job.salary) == ("linkedin", None)


# a page without the usual header is rejected
def test_parse_job_details_rejects_unexpected_text():
    with pytest.raises(ValueError):
        parse_job_details(1, "Sign in\n\nJoin now")


# MCP stand-in: search results per keywords, details per job ID; IDs in `fail` return an error result
class FakeServer:
    def __init__(self, searches, fail=(), search_error=None):
        self.searches = searches
        self.fail = set(fail)
        self.search_error = search_error
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "search_jobs":
            if self.search_error:
                raise LinkedInError(self.search_error)
            return {"job_ids": self.searches[args["keywords"]]}
        if args["job_id"] in self.fail:
            raise LinkedInError("page failed")
        return {"sections": {"job_posting": DETAILS}}


CONFIG = LinkedInConfig([LinkedInSearch("python in Slovakia", work_type="remote,hybrid"), LinkedInSearch("c#")])


def run(server, known=lambda ids: set()):
    jobs = []
    asyncio.run(collect(server, CONFIG, known, jobs))
    return jobs


# IDs are deduplicated across searches, stored ones skip details, and filters reach the search
def test_collect_skips_known_and_duplicate_ids(caplog):
    caplog.set_level("INFO")
    server = FakeServer({"python in Slovakia": ["1", "2"], "c#": ["2", "3"]})

    jobs = run(server, known=lambda ids: {i for i in ids if i == 1})

    assert [j.id for j in jobs] == [2, 3]
    assert "linkedin search 'c#': 2 job ids" in caplog.text
    assert "3 unique job ids, 1 already stored, 2 to read" in caplog.text
    assert "linkedin job 2 parsed: Back End JavaScript Developer | Ciklum | Bratislava, Slovakia | 1 day ago | Hybrid, Full-time" in caplog.text
    assert [a["job_id"] for name, a in server.calls if name == "get_job_details"] == ["2", "3"]
    assert server.calls[0] == ("search_jobs", {
        "keywords": "python in Slovakia", "date_posted": "past_24_hours", "max_pages": 1, "work_type": "remote,hybrid",
    })


# one failed details page skips only that job
def test_collect_skips_a_failed_job():
    server = FakeServer({"python in Slovakia": ["1", "2"], "c#": []}, fail={"1"})

    assert [j.id for j in run(server)] == [2]


# a login prompt during search ends LinkedIn for this run with no jobs and no exception
def test_fetch_jobs_returns_nothing_on_login_error(monkeypatch, caplog):
    async def fake_fetch(config, known, jobs):
        await collect(FakeServer({}, search_error="A LinkedIn login window is open"), config, known, jobs)

    monkeypatch.setattr(linkedin, "_fetch", fake_fetch)

    assert fetch_jobs(CONFIG, lambda ids: set()) == []
    assert "login window" in caplog.text
